"""The shuffle round, with marked songs more often. `python -m radio.shuffle <file>` prints the file's lines (one
playlist line each) in air order; Liquidsoap asks at the start of every round and falls back to its own shuffle.

Each source is shuffled on its own and the two alternate (as Liquidsoap does). A song marked in the last 30 days
comes up `boost` times a round (Settings), its plays at least a quarter of the round apart and never back to back;
every other song still plays once. Extra plays are at most 30% of the round, given by most marks, then the latest
mark; a round under 8 songs gets none."""
import json
import random
import re
import sys
from datetime import timedelta

from . import config, settings, store

MIN_ROUND = 8
EXTRA_SHARE = .3
DAYS = 30
REF = re.compile(r'(?:^|:|,)ref=("(?:[^"\\\r\n]|\\.)*")')


def ref(line: str) -> str:
    match = REF.search(line)
    try:
        return json.loads(match.group(1)) if match else line
    except json.JSONDecodeError:
        return line


def base(lines: list[str], rng: random.Random) -> list[str]:
    remote = [line for line in lines if 'ytmusic:' in line]
    local = [line for line in lines if 'ytmusic:' not in line]
    rng.shuffle(remote)
    rng.shuffle(local)
    keyed = [(2 * i, line) for i, line in enumerate(remote)] + [(2 * i + 1, line) for i, line in enumerate(local)]
    return [line for _, line in sorted(keyed, key=lambda x: x[0])]


def marked(db, at) -> dict:
    """ref -> (marks, last mark) over the last 30 days."""
    return {r[0]: (r[1], r[2]) for r in db.execute('SELECT ref, COUNT(*), MAX(at) FROM marks WHERE at>? GROUP BY ref',
                                                   (store.iso(at - timedelta(days=DAYS)),))}


def _plays(length: int, count: int, gap: int, taken: set, rng: random.Random):
    """count free positions in 0..length-1, consecutive ones at least gap apart, none at 0 (it follows the previous
    round's last song); None if they do not fit."""
    widest = (length - 2) // (count - 1) if count > 1 else 0
    for _ in range(200):
        step = rng.randint(gap, widest) if widest >= gap else None
        if step is None:
            return None
        start = rng.randint(1, length - 1 - (count - 1) * step)
        spots = [start + j * step for j in range(count)]
        if not taken.intersection(spots):
            return spots
    return None


def order(lines: list[str], marks: dict, boost: int, rng: random.Random) -> list[str]:
    round_ = base(lines, rng)
    size = len(round_)
    first = {}
    for line in round_:
        first.setdefault(ref(line), line)
    picked = sorted((r for r in first if r in marks), key=lambda r: marks[r][1], reverse=True)
    picked.sort(key=lambda r: marks[r][0], reverse=True)  # most marks first, then the latest mark
    if size < MIN_ROUND or boost < 2 or not picked:
        return round_
    budget, extra = int(EXTRA_SHARE * size), {}
    for r in picked:
        if budget <= 0:
            break
        extra[r] = min(boost - 1, budget)
        budget -= extra[r]
    while True:  # place the marked songs' plays first; a song that does not fit gets fewer, then the round is redone
        length = size + sum(extra.values())
        slots, failed = {}, None
        for r, more in extra.items():
            spots = _plays(length, more + 1, length // 4, set(slots), rng)
            if spots is None:
                failed = r
                break
            slots.update((spot, first[r]) for spot in spots)
        if failed is None:
            break
        extra[failed] -= 1
        if not extra[failed]:
            del extra[failed]
    rest = iter(line for line in round_ if ref(line) not in extra)
    return [slots[i] if i in slots else next(rest) for i in range(length)]


def main(path: str) -> None:
    lines = [line for line in open(path, encoding='utf-8').read().splitlines() if line.strip()]
    cfg = config.load()
    settings.apply(cfg, settings.base(cfg))
    db = store.connect()
    try:
        marks = marked(db, store.now())
    finally:
        db.close()
    print('\n'.join(order(lines, marks, int(cfg['music']['marked_boost']), random.Random())))


if __name__ == '__main__':
    main(sys.argv[1])
