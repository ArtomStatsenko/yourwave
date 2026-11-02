"""Charts: songs ranked by thumbs up over a rolling window, with their place a day earlier."""
from datetime import timedelta

from . import music, store

WINDOWS = {'week': 7, 'month': 30, 'all': None}


def ranking(db, days, at) -> list[dict]:
    """Songs marked in the `days` before `at` (all time: None): most marks first, then by title."""
    since = store.iso(at - timedelta(days=days)) if days else ''
    rows = db.execute('SELECT ref, COUNT(*) AS marks FROM marks WHERE at>? AND at<=? GROUP BY ref', (since, store.iso(at))).fetchall()
    ranked = sorted(({'ref': r['ref'], 'title': music.label(db, r['ref']), 'marks': r['marks']} for r in rows),
                    key=lambda r: (-r['marks'], r['title'].casefold()))
    for place, r in enumerate(ranked, 1):
        r['place'] = place
    if ranked:
        plays = dict(db.execute(f"SELECT ref, COUNT(*) FROM events WHERE type='MUSIC_STARTED' AND at>? AND at<=? "
                                f"AND ref IN ({','.join('?' * len(ranked))}) GROUP BY ref", (since, store.iso(at), *[r['ref'] for r in ranked])))
        for r in ranked:
            r['plays'] = plays.get(r['ref'], 0)
    return ranked


def listing(db, at=None) -> dict:
    """{week, month, all}: [{place, ref, title, marks, plays, was}]; was: the place a day earlier, None if unranked."""
    at = at or store.now()
    out = {}
    for key, days in WINDOWS.items():
        before = {r['ref']: r['place'] for r in ranking(db, days, at - timedelta(days=1))}
        out[key] = [{**r, 'was': before.get(r['ref'])} for r in ranking(db, days, at)]
    return out
