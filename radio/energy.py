"""Offline music analysis; the on-air selector uses only the resulting JSON, without librosa."""
import argparse
import functools
import hashlib
import json
import math
import re
import subprocess
import time
from pathlib import Path

from . import audio, config, music

PERIOD_SEC = 30 * 60
VERSION = 1

GENRES = {
    'hip-hop': r'hip[ -]?hop|rap|trap|хип[ -]?хоп|рэп|трэп',
    'metal': r'metal|метал',
    'rock': r'rock|punk|grunge|рок|панк',
    'electronic': r'electro\w*|techno|house|trance|dubstep|drum.?and.?bass|dnb|edm|synth\w*|darkwave|dance|электрон\w*|техно|хаус',
    'jazz': r'jazz|swing|джаз',
    'soul/funk': r'soul|funk|r&b|rnb|соул|фанк',
    'pop': r'pop|поп',
    'folk/acoustic': r'folk|acoustic|singer.songwriter|фолк|акуст\w*',
    'classical': r'classical|orchestral|symphon\w*|baroque|классич\w*|оркестр\w*',
    'ambient': r'ambient|chill\w*|downtempo|эмбиент',
    'reggae': r'reggae|ska|регги',
    'country': r'country|bluegrass|кантри',
    'blues': r'blues|блюз',
    'latin': r'latin|salsa|bossa|reggaeton|латин\w*',
    'experimental': r'experimental|avant.?garde|avantgarde|эксперимент\w*',
}


def genre_family(tag):
    """Suno genre tags can contain a whole production prompt; use its first recognizable musical family."""
    matches = [(m.start(), name) for name, pattern in GENRES.items()
               if (m := re.search(r'\b(?:' + pattern + r')\b', tag.casefold()))]
    return min(matches)[1] if matches else ''


def signature(path):
    st = Path(path).stat()
    return f'{st.st_size}:{st.st_mtime_ns}'


@functools.lru_cache(maxsize=1)
def _read(path, stamp):
    data = json.loads(Path(path).read_text())
    rows = data.get('tracks', {})
    return {k: v for k, v in rows.items() if isinstance(v, dict)} if data.get('version') == VERSION and isinstance(rows, dict) else {}


def cached():
    path = config.DATA_DIR / 'energy.json'
    try:
        return _read(str(path), path.stat().st_mtime_ns)
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def profiles(files):
    known, result = cached(), {}
    for path in files:
        row = known.get(str(path), {})
        try:
            if (row.get('sig') == signature(path) and isinstance(row.get('energy'), int)
                    and 1 <= row['energy'] <= 5):
                result[str(path)] = row
        except OSError:
            continue
    return result


def target(elapsed, until_voice=None):
    value = 3 - 2 * math.cos(2 * math.pi * elapsed / PERIOD_SEC)
    if until_voice is not None:
        value = min(value, 1.5 + 3.5 * min(1, max(0, until_voice) / 300))
    return value


def duration(row):
    return row.get('duration') or 180.0


def order(files, known, elapsed=0.0, previous=None, recency=None, until_voice=None):
    """Weighted, reproducible choices on a musical clock; batching never resets clock or previous sound."""
    pending, result = list(files), []
    recency = recency or {}
    while pending:
        def cost(path):
            row = known.get(str(path), {})
            level = row.get('energy', 3)
            midpoint = duration(row) / 2
            goal = target(elapsed + midpoint, until_voice - midpoint if until_voice is not None else None)
            distance = .8 * (level - goal)**2 + .5 * recency.get(str(path), 0)
            if previous:
                distance += .35 * (level - previous.get('energy', 3))**2
                group = row.get('genre') or row.get('family')
                before = previous.get('genre') or previous.get('family')
                if group and before and group != before:
                    distance += .65
                a, b = row.get('timbre', []), previous.get('timbre', [])
                if a and len(a) == len(b):
                    distance += .2 * min(2, math.sqrt(sum((x-y)**2 for x, y in zip(a, b)) / len(a)))
            # Exponential race: every level has nonzero weight, without batch-dependent RNG state.
            digest = hashlib.blake2b(f'{path}:{elapsed:.3f}'.encode(), digest_size=8).digest()
            uniform = (int.from_bytes(digest) % (2**53 - 1) + 1) / 2**53
            return -math.log(uniform) * math.exp(distance)

        picked = min(pending, key=cost)
        pending.remove(picked)
        result.append(picked)
        previous = known.get(str(picked), {})
        seconds = duration(previous)
        elapsed += seconds
        if until_voice is not None:
            until_voice -= seconds
    return result


def measure(path):
    import librosa
    import numpy as np

    duration = audio.duration(path)
    # shortcut: the middle minute represents a track; sample several sections if listening shows bad ratings.
    pcm = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(max(0, (duration-60)/2)),
                          '-i', str(path), '-t', '60', '-ac', '1', '-ar', '22050', '-f', 'f32le', '-'],
                         capture_output=True, check=True, timeout=120).stdout
    y = np.frombuffer(pcm, dtype='<f4').copy()
    if len(y) < 22050 or not np.isfinite(y).all() or np.max(np.abs(y)) < .0001:
        raise ValueError('too short or silent')
    y /= np.max(np.abs(y))
    spectrum = librosa.power_to_db(librosa.feature.melspectrogram(y=y, sr=22050), ref=np.max)
    onset = librosa.onset.onset_strength(S=spectrum, sr=22050)
    bpm, _ = librosa.beat.beat_track(onset_envelope=onset, sr=22050)
    raw = librosa.feature.mfcc(S=spectrum, n_mfcc=9)[1:].mean(axis=1).tolist()
    tags = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags=genre:stream_tags=genre',
                           '-of', 'json', str(path)], capture_output=True, text=True, check=True, timeout=30)
    data = json.loads(tags.stdout)
    genre = ''
    for entry in [s.get('tags', {}) for s in data.get('streams', [])] + [data.get('format', {}).get('tags', {})]:
        genre = next((v.strip().casefold() for k, v in entry.items() if k.casefold() == 'genre' and v.strip()), genre)
    genre = genre_family(genre)
    return dict(bpm=float(np.asarray(bpm).item()), activity=float(onset.mean()), raw_timbre=raw, duration=duration,
                genre=genre, genre_source='tag' if genre else 'sound similarity')


def rate(rows):
    """Relative quintiles; timbre clusters are deliberately not presented as recognized genres."""
    import numpy as np
    from sklearn.cluster import KMeans

    values = list(rows.values())
    if not values:
        return
    ranks = []
    for key in ('bpm', 'activity'):
        series = [r[key] for r in values]
        ranks.append([sum(v < x for v in series) / max(1, len(series)-1) for x in series])
    scores = [.4*b + .6*a for b, a in zip(*ranks)]
    raw = np.array([r['raw_timbre'] for r in values])
    normalized = (raw - raw.mean(axis=0)) / np.maximum(raw.std(axis=0), 1)
    count = min(5, max(1, len(values)//5), len(np.unique(raw, axis=0)))
    groups = KMeans(n_clusters=count, random_state=0, n_init=10).fit_predict(normalized)
    for row, score, timbre, group in zip(values, scores, normalized.tolist(), groups):
        rank = sum(s < score for s in scores) / max(1, len(scores))
        row['energy'] = row.get('energy_override') or min(5, 1 + int(rank*5))
        row['timbre'] = timbre
        row['family'] = f'sound group {int(group)+1}'
        row['genre'] = genre_family(row.get('genre', ''))
        row['genre_source'] = 'tag' if row['genre'] else 'sound similarity'
        if row.get('genre_override'):
            row['genre'], row['genre_source'] = row['genre_override'], 'manual'


def analyze():
    cfg = config.load()
    files, previous, rows = music.scan(cfg['music']['path']), cached(), {}
    for index, path in enumerate(files, 1):
        try:
            sig = signature(path)
            row = previous.get(str(path), {})
            if row.get('sig') != sig or 'raw_timbre' not in row:
                measured = measure(path)
                if signature(path) != sig:
                    raise ValueError('file changed during analysis')
                row = dict(measured, sig=sig, **{k: row[k] for k in ('energy_override', 'genre_override') if k in row})
            if not row.get('duration'):
                row = dict(row, duration=audio.duration(path))
            rows[str(path)] = row
            print(f'{index}/{len(files)} analyzed', flush=True)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            print(f'{index}/{len(files)} skipped: {type(error).__name__}', flush=True)
    rate(rows)
    config.write_json(config.DATA_DIR / 'energy.json', dict(version=VERSION, updated_at=time.time(), tracks=rows))
    print(f'Ready: {len(rows)}/{len(files)} tracks; no playback changes.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('analyze', 'report', 'set'))
    parser.add_argument('--path', type=Path)
    parser.add_argument('--energy', type=int, choices=range(1, 6))
    parser.add_argument('--genre')
    args = parser.parse_args()
    if args.command == 'analyze':
        analyze()
    elif args.command == 'report':
        for path, row in cached().items():
            print(f"{row['energy']}  {row['bpm']:.0f} BPM  {row.get('genre') or row['family']}  {path}")
    else:
        rows = dict(cached())
        requested = args.path.expanduser().resolve() if args.path else None
        path = next((p for p in rows if Path(p).resolve() == requested), '')
        if path not in rows or (args.energy is None and not (args.genre and args.genre.strip())):
            parser.error('set needs an analyzed --path and --energy and/or --genre')
        rows[path] = dict(rows[path])
        if args.energy is not None:
            rows[path]['energy'] = rows[path]['energy_override'] = args.energy
        if args.genre:
            rows[path].update(genre=args.genre.strip().casefold(), genre_override=args.genre.strip().casefold(), genre_source='manual')
        config.write_json(config.DATA_DIR / 'energy.json', dict(version=VERSION, updated_at=time.time(), tracks=rows))


if __name__ == '__main__':
    main()
