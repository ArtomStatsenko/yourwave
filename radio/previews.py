"""Close a host edition with one later hook, then introduce the first song."""
import hashlib
import json
from pathlib import Path

from . import audio, config, hooks


def render(db, snapshot, titles, say):
    first = say('А сейчас — ' + titles[0] + '.')
    speech = audio.duration(first)
    if not 0 < speech <= 25:
        return None
    pieces, included = [], []
    item = snapshot[1]
    row = hooks.ready(db, item['ref'], item['path']) if item['ref'] != snapshot[0]['ref'] else None
    if row:
        seconds = audio.duration(Path(row['clip']))
        if 5 <= seconds <= 12:
            voice = say('Позже в этом музыкальном блоке — ' + titles[1] + '.')
            later_speech = audio.duration(voice)
            if later_speech > 0 and speech + later_speech + seconds <= 25:
                pieces.extend([voice, Path(row['clip'])])
                included.append(row['id'])
                speech += later_speech
    pieces.append(first)
    key = hashlib.sha256(json.dumps([titles, [str(p) for p in pieces]], ensure_ascii=False).encode()).hexdigest()[:24]
    out = config.DATA_DIR / 'cache/packages' / ('preview-' + key + '.wav')
    path = audio.join_packages(pieces, out)
    duration = audio.duration(path)
    if not 0 < duration <= 25.05:
        return None
    return {'audio_path': str(path), 'speech_sec': speech, 'duration_sec': duration, 'hook_ids': included,
            'announced_refs': [snapshot[0]['ref']] + ([snapshot[1]['ref']] if included else []),
            'announced_titles': [titles[0]] + ([titles[1]] if included else [])}


def uri(job):
    refs = json.dumps([x['ref'] for x in json.loads(job['snapshot'])], ensure_ascii=False, separators=(',', ':'))
    tags = {'kind': 'teaser', 'ref': job['id'], 'preview_refs': refs,
            'preview_after': job['after_news'],
            'preview_files': job['snapshot'],
            'air_until': str(job['expires']), 'liq_fade_in': '0.03', 'liq_fade_out': '0.1',
            'liq_cross_start_duration': '0.1', 'liq_cross_end_duration': '0.1'}
    return 'annotate:' + ','.join(k + '=' + json.dumps(v, ensure_ascii=False) for k,v in tags.items()) + ':' + job['audio_path']
