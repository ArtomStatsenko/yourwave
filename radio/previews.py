"""Render a prepared coming-up montage; the playback engine validates its queue at handoff."""
import hashlib
import json
from pathlib import Path

from . import audio, config, hooks


def render(db, snapshot, titles, say):
    voices = [say('Скоро в эфире. ' + titles[0] + '.'), say('И ' + titles[1] + '.')]
    speech = sum(audio.duration(v) for v in voices)
    if speech > 25 or speech <= 0:
        return None
    pieces, total, included = [], speech, []
    for item, voice in zip(snapshot, voices):
        pieces.append(voice)
        row = hooks.ready(db, item['ref'], item['path'])
        if row:
            seconds = audio.duration(Path(row['clip']))
            if 5 <= seconds <= 12 and total + seconds <= 25:
                pieces.append(Path(row['clip']))
                included.append(row['id'])
                total += seconds
    key = hashlib.sha256(json.dumps([titles, [str(p) for p in pieces]], ensure_ascii=False).encode()).hexdigest()[:24]
    out = config.DATA_DIR / 'cache/packages' / ('preview-' + key + '.wav')
    path = audio.join_packages(pieces, out)
    duration = audio.duration(path)
    if not 0 < duration <= 25.05:
        return None
    return {'audio_path': str(path), 'speech_sec': speech, 'duration_sec': duration, 'hook_ids': included}


def uri(job):
    refs = json.dumps(json.loads(job['refs']), ensure_ascii=False, separators=(',', ':'))
    tags = {'kind': 'teaser', 'ref': job['id'], 'preview_refs': refs,
            'preview_files': job['snapshot'],
            'air_until': str(job['expires']), 'liq_fade_in': '0.03', 'liq_fade_out': '0.1',
            'liq_cross_start_duration': '0.1', 'liq_cross_end_duration': '0.1'}
    return 'annotate:' + ','.join(k + '=' + json.dumps(v, ensure_ascii=False) for k,v in tags.items()) + ':' + job['audio_path']
