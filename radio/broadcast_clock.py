"""Calendar occurrences shared by playout and the editor, independent of process uptime."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import store
from .content import parse_time

WINDOW = timedelta(minutes=5)
PREFIX = 'section:news:clock:'
TERMINAL = {'PLAYED', 'EXPIRED', 'INTERRUPTED'}


def rows(db, at, hours=6, timezone_name='Asia/Ho_Chi_Minh'):
    zone = ZoneInfo(timezone_name)
    local = at.astimezone(zone)
    start = local.replace(minute=local.minute // 30 * 30, second=0, microsecond=0).astimezone(timezone.utc)
    changes = {r['id']: dict(r) for r in db.execute("SELECT * FROM air_plan WHERE id LIKE ?", (PREFIX + '%',))}
    targets = {PREFIX + str(int((start + timedelta(minutes=30 * n)).timestamp())):
               start + timedelta(minutes=30 * n) for n in range(hours * 2 + 1)}
    targets.update({key: datetime.fromtimestamp(int(key.removeprefix(PREFIX)), timezone.utc) for key in changes})
    result = []
    for key, target in targets.items():
        edit = changes.get(key, {})
        when = parse_time(edit.get('not_before')) or target
        if not at < when + WINDOW or when > at + timedelta(hours=hours) or edit.get('status') in TERMINAL:
            continue
        light = target.astimezone(zone).minute == 30
        result.append({'id': key, 'kind': 'news', 'ref': edit.get('ref', ''), 'title': 'Host edition',
                       'at': store.iso(when), 'not_before': store.iso(when), 'ends': store.iso(when + WINDOW),
                       'duration': 45 if light else 120, 'light': light, 'status': edit.get('status', 'PENDING'),
                       'skipped': bool(edit.get('skipped')), 'locked': edit.get('status') == 'QUEUED',
                       'members': [key], 'placeholder': True, 'estimated': False, 'clock': True,
                       'edited': bool(edit), 'rid': edit.get('rid')})
    return sorted(result, key=lambda r: r['at'])
