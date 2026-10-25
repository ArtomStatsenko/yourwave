"""Local overrides and scheduled inserts for the experimental running order."""
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from . import content, plugins, store, weather


def requested_time(value, at):
    due = content.parse_time(value or '')
    if due is None or due < at - timedelta(minutes=1):
        raise ValueError('Choose a current or future time')
    return due


def edits(db) -> dict:
    return {r['id']: dict(r) for r in db.execute('SELECT * FROM air_plan')}


def allowed(row, changes, at):
    edit = changes.get(row['id'], {})
    return not edit.get('skipped') and (not edit.get('not_before') or edit['not_before'] <= store.iso(at))


def ordered(rows, changes):
    return sorted(rows, key=lambda r: changes.get(r['id'], {}).get('rank')
                  if changes.get(r['id'], {}).get('rank') is not None else 1_000_000 + rows.index(r))


def news(db):
    return [dict(r) for r in db.execute(f'''SELECT p.*,
               i.topic AS title,i.kind AS item_kind,i.source,i.published_at FROM packages p JOIN items i ON i.id=p.item_id
               WHERE p.status IN ('PENDING','READY','QUEUED') AND i.state='NEW' ORDER BY {content.ROTATION}''')]


def weather_rows(db, cfg, at, changes):
    start = weather.slot(at, cfg['weather'].get('timezone', 'Asia/Ho_Chi_Minh'))
    fired = {r[0] for r in db.execute("SELECT key FROM reminders WHERE key LIKE 'weather:slot:%'")}
    rows = []
    slots = {f'weather:slot:{int((start + timedelta(hours=n * 3)).timestamp())}': start + timedelta(hours=n * 3) for n in range(-8, 9)}
    slots.update({key: datetime.fromtimestamp(int(key.rsplit(':', 1)[1]), start.tzinfo)
                  for key in changes if key.startswith('weather:slot:')})
    for key, slot in slots.items():
        due = changes.get(key, {}).get('not_before') or store.iso(slot)
        if key not in fired and store.iso(at - timedelta(minutes=5)) < due and (due <= store.iso(at + timedelta(days=1)) or bool(changes.get(key, {}).get('not_before'))):
            rows.append({'id': key, 'kind': 'weather', 'ref': '', 'title': 'Weather · ' + cfg['weather']['city'],
                         'not_before': due, 'status': 'PENDING'})
    return sorted(rows, key=lambda r: r['not_before'])


def rows(db, cfg, at):
    changes = edits(db) if plugins.on(cfg, 'air_direction') else {}
    result = []
    off = {source for source, feature in plugins.SOURCE_PLUGIN.items() if not plugins.on(cfg, feature)}
    for row in ordered([r for r in news(db) if r['source'] not in off], changes):
        script = Path(row['script_path']) if row['script_path'] else None
        preview = row['prepared_text'][:240] if row['prepared_text'] is not None else script.read_text(encoding='utf-8')[:240] if script and script.exists() else ''
        result.append({**row, 'kind': 'news', 'preview': preview})
    if cfg['weather']['city'] and plugins.on(cfg, 'weather'):
        result += weather_rows(db, cfg, at, changes)
    if plugins.on(cfg, 'air_direction'):
        result += [dict(r) for r in db.execute("SELECT *,text AS title FROM air_plan WHERE (id LIKE 'direction:%' OR id LIKE 'section:%') "
                                             "AND status IN ('PENDING','QUEUED','PREPARING','ERROR')")]
    return [{**r, 'ref': changes.get(r['id'], {}).get('ref') or r.get('ref', ''),
             'rid': changes.get(r['id'], {}).get('rid') or r.get('rid'), 'not_before': changes.get(r['id'], {}).get('not_before') or r.get('not_before'),
             'status': changes.get(r['id'], {}).get('status') if changes.get(r['id'], {}).get('status') in ('QUEUED','ERROR','PREPARING') else r['status'],
             'skipped': bool(changes.get(r['id'], {}).get('skipped')), 'edited': r['id'] in changes}
            for r in result]


def edit(db, cfg, ref, action, at, value=None):
    current = rows(db, cfg, at)
    row = next((r for r in current if r['id'] == ref), None)
    if not row:
        raise ValueError('Block is no longer available')
    if row['status'] == 'QUEUED':
        raise ValueError('Block is already in the playback queue')
    if action == 'reset':
        if ref.startswith('direction:'):
            db.execute('UPDATE air_plan SET skipped=0,status=\'PENDING\',error=NULL WHERE id=?', (ref,))
        else:
            db.execute('DELETE FROM air_plan WHERE id=?', (ref,))
        return
    if action not in ('up', 'down', 'skip', 'time'):
        raise ValueError('Unknown running-order action')
    if action in ('up', 'down') and row['kind'] != 'news':
        raise ValueError('Reordering applies to news; use the time for other blocks')
    due = None
    if action == 'time':
        due = requested_time(value, at)
    db.execute('INSERT OR IGNORE INTO air_plan(id,kind,ref) VALUES(?,?,?)', (ref, row['kind'], ref))
    if action in ('up', 'down'):
        queue = [r for r in current if r['kind'] == 'news']
        i = next(i for i, r in enumerate(queue) if r['id'] == ref)
        j = i + (-1 if action == 'up' else 1)
        if not 0 <= j < len(queue):
            return
        queue[i], queue[j] = queue[j], queue[i]
        with db:
            for rank, entry in enumerate(queue):
                db.execute('INSERT INTO air_plan(id,kind,ref,rank) VALUES(?,?,?,?) '
                           'ON CONFLICT(id) DO UPDATE SET rank=excluded.rank',
                           (entry['id'], 'news', entry['id'], rank))
    elif action == 'skip':
        db.execute('UPDATE air_plan SET skipped=1-skipped WHERE id=?', (ref,))
    elif action == 'time':
        db.execute('UPDATE air_plan SET not_before=?,status=\'PENDING\',error=NULL WHERE id=?', (store.iso(due), ref))
    else:
        raise ValueError('Unknown running-order action')


def insert(db, cfg, kind, ref, text, at, due):
    when = requested_time(due, at)
    key = 'direction:' + uuid.uuid4().hex
    if kind == 'text' or kind == 'news' and ref:
        published, title = at, 'Host announcement'
        item_kind = 'scheduled'
        if kind == 'news':
            original = db.execute('SELECT p.script_path,p.prepared_text,i.kind,i.topic,i.published_at FROM packages p JOIN items i '
                                  'ON i.id=p.item_id WHERE p.id=?', (ref,)).fetchone()
            if not original or not original['script_path'] or not Path(original['script_path']).exists():
                raise ValueError('Bulletin text is unavailable')
            text = original['prepared_text'] if original['prepared_text'] is not None else Path(original['script_path']).read_text()
            title = 'Replay · ' + (original['topic'] or 'Bulletin')
            if original['prepared_text'] is None and original['kind'] in ('raw_post', 'ready_digest'):
                item_kind = original['kind']
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Enter 1–4000 characters')
        item_id = 'system:' + key
        content.add_item(db, cfg, {'id': item_id, 'topic': title, 'speak_topic': False, 'source': 'direction',
                                  'url': '', 'published_at': published, 'body': text}, kind=item_kind)
        for p in db.execute('SELECT id FROM packages WHERE item_id=?', (item_id,)).fetchall():
            db.execute('INSERT INTO air_plan(id,kind,ref,not_before,rank) VALUES(?,?,?,?,?)',
                       (p['id'], 'news', p['id'], store.iso(when), -1))
        return item_id
    if kind not in ('news', 'weather', 'music', 'jingle', 'reminder'):
        raise ValueError('Unknown block type')
    db.execute('INSERT INTO air_plan(id,kind,ref,text,not_before) VALUES(?,?,?,?,?)',
               (key, kind, ref, text, store.iso(when)))
    return key
