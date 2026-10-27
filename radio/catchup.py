"""Manual dated catch-up from the local news archive; originals stay unplayed until completion."""
import json
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config, content, github_trending, store

SOURCES = ('reddit','telegram','horizon','github_trending','github_new','inbox')


def active(db):
    return db.execute("SELECT i.id,p.status FROM items i JOIN packages p ON p.item_id=i.id "
                      "WHERE i.source='catchup' AND p.status IN ('PENDING','READY','QUEUED','PLAYING') "
                      "ORDER BY CASE p.status WHEN 'PLAYING' THEN 0 WHEN 'QUEUED' THEN 1 WHEN 'PENDING' THEN 2 ELSE 3 END,i.created_at LIMIT 1").fetchone()


def origins(db, item_id):
    row = db.execute("SELECT origin_json FROM items WHERE id=? AND source='catchup'",(item_id,)).fetchone()
    return json.loads(row[0] or '[]') if row else []


def reserved(db):
    return {o['item_id'] for row in db.execute("SELECT DISTINCT i.origin_json FROM items i JOIN packages p ON p.item_id=i.id "
            "WHERE i.source='catchup' AND p.status IN ('PENDING','READY','QUEUED','PLAYING')")
            for o in json.loads(row[0] or '[]') if o.get('item_id')}


def candidates(db, at, off=()):
    reserved_ids = reserved(db)
    rows = db.execute("SELECT i.*, (SELECT count(*) FROM packages WHERE item_id=i.id AND prepared_text IS NULL) AS unprepared, "
                      "(SELECT group_concat(prepared_text,char(10)) FROM "
                      "(SELECT prepared_text FROM packages WHERE item_id=i.id ORDER BY part)) AS prepared "
                      "FROM items i WHERE i.published_at>=? AND i.published_at<=? AND i.state IN ('NEW','EXPIRED') "
                      "AND NOT EXISTS(SELECT 1 FROM packages p WHERE p.item_id=i.id AND p.status IN ('PLAYED','PLAYING','QUEUED')) "
                      "AND NOT EXISTS(SELECT 1 FROM events e JOIN packages p ON p.id=e.ref WHERE p.item_id=i.id AND e.type='PACKAGE_STARTED') "
                      "AND NOT EXISTS(SELECT 1 FROM events e WHERE e.type='CATCHUP_ITEM' AND e.ref=i.id) "
                      "ORDER BY i.published_at DESC,i.id",(store.iso(at-timedelta(hours=48)),store.iso(at))).fetchall()
    return [dict(r) for r in rows if r['source'] in SOURCES and r['source'] not in off and r['id'] not in reserved_ids
            and (r['source'] not in ('github_trending','github_new') or github_trending.dated_event(r['origin_json']))]


def source_text(db, row):
    if row.get('prepared') and not row.get('unprepared'):
        return row['prepared'], True
    descriptions = [o.get('description','') for o in json.loads(row['origin_json'] or '[]')]
    if any(descriptions):
        return '\n'.join(descriptions), False
    scripts = []
    for p in db.execute('SELECT script_path FROM packages WHERE item_id=? ORDER BY part',(row['id'],)):
        path = Path(p[0] or '').resolve()
        if path.is_relative_to((config.DATA_DIR/'cache/scripts').resolve()) and path.is_file():
            scripts.append(path.read_text())
    return '\n'.join(scripts), False


def prepare(db, cfg, at, off=()):
    zone = ZoneInfo(cfg['weather'].get('timezone','Asia/Ho_Chi_Minh'))
    selected = []
    for row in candidates(db,at,off)[:12]:
        raw, _ = source_text(db,row)
        if not raw or len(raw) > 8000 or sum(len(r['source_text']) for r in selected)+len(raw)>18000:
            continue
        published = content.parse_time(row['published_at']).astimezone(zone)
        selected.append({**row,'source_text':raw,'date_label':published.strftime('%Y-%m-%d')})
        if len(selected)==6:
            break
    return selected


def complete(db, package_id, session):
    row = db.execute("SELECT i.id FROM items i JOIN packages p ON p.item_id=i.id WHERE p.id=? AND i.source='catchup'",(package_id,)).fetchone()
    if not row or db.execute("SELECT 1 FROM packages WHERE item_id=? AND status!='PLAYED'",(row[0],)).fetchone():
        return
    for original in origins(db,row[0]):
        store.log_event(db,session,'CATCHUP_ITEM',original['item_id'],row[0])
        db.execute("UPDATE packages SET status='SKIPPED',error='Covered by completed catch-up' WHERE item_id=? AND status IN ('PENDING','READY')",(original['item_id'],))
        content.finish_item_if_done(db,original['item_id'])
