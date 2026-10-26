"""Offline preparation and review of short music hooks. Nothing here runs at an audio handoff."""
import argparse
import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import audio, config, store
from .hook_alignment import chorus, map_sections, parse

ALGORITHM = 'lyrics-global-v1'


def missing_clip(row):
    return row['review'] in ('APPROVED','AUTO','CANDIDATE') and (not row['clip'] or not Path(row['clip']).is_file())


def signature(path):
    s = Path(path).stat()
    return f'{s.st_size}:{s.st_mtime_ns}'


def pcm_hash(path):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-map', '0:a:0',
                        '-ar', '44100', '-ac', '2', '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'],
                       capture_output=True, text=True, timeout=120, check=True)
    return r.stdout.strip().split('=')[-1]


def ready(db, ref, path=None):
    rows = db.execute("SELECT * FROM track_hooks WHERE ref=? AND review IN ('APPROVED','AUTO') "
                      "ORDER BY review='APPROVED' DESC,id DESC", (ref,)).fetchall()
    for row in rows:
        try:
            if (path is None or Path(path) == Path(row['source_path'])) and signature(row['source_path']) == row['source_sig'] and row['clip'] and Path(row['clip']).is_file():
                return row
        except OSError:
            pass
    return None


def export(path, start, end, target):
    length = float(end) - float(start)
    if not 0 <= float(start) < float(end) <= audio.duration(Path(path)) + .01 or not 5 <= length <= 12:
        raise ValueError('Hook must be 5–12 seconds within the recording')
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp.wav')
    audio._ffmpeg('-y', '-ss', str(start), '-i', str(path), '-t', str(length), '-af',
                  f'loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:d=0.035,afade=t=out:st={length-.15}:d=0.15',
                  '-c:a', 'pcm_s16le', str(tmp))
    os.replace(tmp, target)


def save(db, ref, path, digest, cue, provider, evidence, review, lyrics_hash=''):
    previous = db.execute('SELECT review FROM track_hooks WHERE ref=? AND audio_hash=? AND algorithm=?', (ref,digest,provider)).fetchone()
    if previous and previous['review'] in ('APPROVED','REJECTED'):
        return
    sig = signature(path)
    if pcm_hash(path) != digest:
        raise ValueError('Source changed before hook export')
    key = hashlib.sha256(f'{ref}:{digest}:{provider}'.encode()).hexdigest()[:24]
    clip = config.DATA_DIR / 'cache/hooks' / (key + '.wav')
    if cue:
        export(path, cue['start'], cue['end'], clip)
    if signature(path) != sig:
        raise ValueError('Source changed during hook export')
    db.execute('INSERT INTO track_hooks(ref,audio_hash,source_path,source_sig,lyrics_hash,algorithm,start,end,provider,evidence,review,clip,retry_after,updated_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(ref,audio_hash,algorithm) DO UPDATE SET '
               'source_path=excluded.source_path,source_sig=excluded.source_sig,lyrics_hash=excluded.lyrics_hash, '
               'start=excluded.start,end=excluded.end,evidence=excluded.evidence,review=excluded.review,clip=excluded.clip, '
               'retry_after=excluded.retry_after,updated_at=excluded.updated_at WHERE track_hooks.review NOT IN (\'APPROVED\',\'REJECTED\')',
               (ref, digest, str(path), sig, lyrics_hash, provider, cue.get('start') if cue else None,
                cue.get('end') if cue else None, provider, json.dumps(evidence, ensure_ascii=False), review,
                str(clip) if cue else None, time.time() + (300 if review == 'ERROR' else 7*86400), store.iso(store.now())))


def songle(db, ref):
    if not ref.startswith('ytmusic:'):
        return []
    old = db.execute("SELECT * FROM hook_lookups WHERE provider='songle' AND lookup_key=?", (ref,)).fetchone()
    if old and old['retry_after'] > time.time():
        return json.loads(old['response']) if old['outcome'] == 'HIT' else []
    url = 'https://widget.songle.jp/api/v1/song/chorus.json?' + urllib.parse.urlencode({'url': 'www.youtube.com/watch?v=' + ref.split(':', 1)[1]})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'PersonalRadio/0.1'}), timeout=15) as response:
            data = json.load(response)
        segments = [s for group in data.get('chorusSegments', []) if group.get('isChorus',True) for s in group.get('repeats', [])]
        cues = [{'start': s['start']/1000, 'end': (s['start'] + min(s['duration'], 8000))/1000} for s in segments if s.get('duration', 0) >= 5000]
        outcome, retry = ('HIT' if cues else 'MISS'), 7*86400
    except (OSError, ValueError, KeyError, TypeError) as error:
        cues = []
        outcome, retry = ('MISS', 7*86400) if isinstance(error, urllib.error.HTTPError) and error.code == 404 else ('TEMP_ERROR', 300)
        if isinstance(error,urllib.error.HTTPError):
            error.close()
    db.execute('INSERT OR REPLACE INTO hook_lookups VALUES(?,?,?,?,?)', ('songle', ref, outcome, json.dumps(cues), time.time()+retry))
    return cues


def transcribe(path, cfg, cache):
    if cache.is_file():
        return json.loads(cache.read_text())
    python = Path(cfg['voice']['server_dir']).expanduser() / '.venv/bin/python'
    script = '''import json,sys,mlx_whisper
r=mlx_whisper.transcribe(sys.argv[1],path_or_hf_repo="mlx-community/whisper-large-v3-turbo",condition_on_previous_text=False,word_timestamps=True)
print(json.dumps([w for s in r['segments'] if s.get('no_speech_prob',1)<.5 and s.get('avg_logprob',-9)>-1 and s.get('compression_ratio',9)<2.4 for w in s.get('words',[]) if w.get('probability',0)>.3]))
'''
    result = subprocess.run(['taskpolicy', '-b', 'nice', '-n', '20', str(python), '-c', script, str(path)],
                            capture_output=True, text=True, timeout=600, check=True, env=dict(os.environ, HF_HUB_OFFLINE='1'))
    words = json.loads(result.stdout.strip().splitlines()[-1])
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_suffix('.tmp.json')
    tmp.write_text(json.dumps(words))
    os.replace(tmp,cache)
    return words


def prepare(ref, path, cfg):
    path = Path(path)
    db = store.connect()
    try:
        sig = signature(path)
        old = db.execute('SELECT * FROM track_hooks WHERE ref=? ORDER BY id DESC', (ref,)).fetchall()
        if any(r['source_path'] == str(path) and r['source_sig'] == sig and r['retry_after'] > time.time() and not missing_clip(r) for r in old):
            return
        digest = pcm_hash(path)
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags', '-of', 'json', str(path)],
                               capture_output=True, text=True, timeout=30, check=True)
        tags = {k.casefold(): v for k,v in json.loads(probe.stdout).get('format', {}).get('tags', {}).items()}
        lyrics = next((v for k,v in tags.items() if k.startswith(('lyrics', 'unsyncedlyrics'))), '')
        lyrics_hash = hashlib.sha256(lyrics.encode()).hexdigest()
        for row in old:
            if row['audio_hash'] == digest and (row['review'] in ('APPROVED','REJECTED') or
                    (row['review'] == 'AUTO' and row['lyrics_hash'] == lyrics_hash and row['algorithm'] == ALGORITHM)):
                db.execute('UPDATE track_hooks SET source_path=?,source_sig=? WHERE id=?', (str(path),sig,row['id']))
                if row['review'] != 'REJECTED' and missing_clip(row):
                    export(path,row['start'],row['end'],Path(row['clip']))
                return
        lines = parse(lyrics)
        cue, provider, evidence, review = None, ALGORITHM, {}, 'NO_HOOK'
        if any(chorus(line['label']) for line in lines):
            try:
                words = transcribe(path, cfg, config.DATA_DIR / 'cache/hooks' / (digest + '-words.json'))
                evidence = map_sections(lines, words, audio.duration(path))
                cue = next((s for s in evidence['sections'] if s['status'] == 'candidate'), None)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                evidence, review = {'asr_error': type(error).__name__}, 'ERROR'
            if cue:
                # shortcut: experimental text thresholds; calibrate against a larger listening sample.
                review = 'AUTO' if evidence['overall_text_coverage'] >= .75 and cue['text_coverage'] >= .95 else 'CANDIDATE'
        if cue is None:
            indexed = db.execute("SELECT response FROM hook_lookups WHERE provider='annotations' AND lookup_key=? AND outcome='HIT'", (ref,)).fetchone()
            cues = json.loads(indexed['response']) if indexed else []
            if cues:
                cue, provider, evidence, review = cues[0], 'annotations', cues[0], 'CANDIDATE'
        if cue is None:
            cues = songle(db, ref)
            if cues:
                cue, provider, evidence, review = cues[0], 'songle', {'identity': 'YouTube ID only; local timing requires review'}, 'CANDIDATE'
            elif ref.startswith('ytmusic:'):
                lookup = db.execute("SELECT outcome FROM hook_lookups WHERE provider='songle' AND lookup_key=?",(ref,)).fetchone()
                if lookup and lookup['outcome'] == 'TEMP_ERROR':
                    review, evidence = 'ERROR', {**evidence,'songle_error':'TEMP_ERROR'}
        if cue is None and cfg.get('hooks', {}).get('analyzer_python'):
            script = '''import allin1,json,sys,torch
torch.set_num_threads(2)
r=allin1.analyze(sys.argv[1],model='harmonix-fold0',device='cpu',multiprocess=False)
print(json.dumps([{'start':s.start,'end':min(s.end,s.start+8)} for s in r.segments if s.label=='chorus' and s.end-s.start>=5]))
'''
            r = subprocess.run([str(Path(cfg['hooks']['analyzer_python']).expanduser()), '-c', script, str(path)], capture_output=True, text=True, timeout=900, check=True)
            cues = json.loads(r.stdout.strip().splitlines()[-1])
            if cues:
                cue, provider, evidence, review = cues[0], 'allin1', {'section': 'chorus', 'requires_listening': True}, 'CANDIDATE'
        if signature(path) != sig:
            raise ValueError('Source changed during analysis')
        save(db, ref, path, digest, cue, provider, evidence, review, lyrics_hash)
    finally:
        db.close()


def listing(db):
    counts = dict(db.execute('SELECT review,COUNT(*) FROM track_hooks GROUP BY review'))
    rows = [dict(r) for r in db.execute("SELECT id,ref,start,end,provider,review FROM track_hooks WHERE review='CANDIDATE' ORDER BY id DESC LIMIT 10")]
    return {'counts': counts, 'candidates': rows}


def review(db, hook_id, approved):
    row = db.execute("SELECT * FROM track_hooks WHERE id=? AND review='CANDIDATE'", (int(hook_id),)).fetchone()
    if not row or signature(row['source_path']) != row['source_sig']:
        raise ValueError('Hook is unavailable or its source changed')
    db.execute('UPDATE track_hooks SET review=? WHERE id=?', ('APPROVED' if approved else 'REJECTED', row['id']))


def import_approved(manifest, source_root):
    db = store.connect()
    try:
        for entry in json.loads(Path(manifest).read_text()):
            source = Path(source_root) / entry['track']
            if not source.is_file():
                continue
            trial = Path(manifest).parent / 'results' / Path(entry['clip']).parent / 'input.wav'
            if not entry.get('approval') or not entry.get('input_sha256') or not trial.is_file() or hashlib.sha256(trial.read_bytes()).hexdigest() != entry['input_sha256']:
                continue
            digest = pcm_hash(source)
            if pcm_hash(trial) == digest:
                save(db, str(source), source, digest, entry, 'user-import', entry, 'APPROVED')
    finally:
        db.close()


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--ref'); p.add_argument('--path'); p.add_argument('--import-approved'); p.add_argument('--source-root')
    p.add_argument('--import-annotations')
    args = p.parse_args()
    cfg = config.load()
    if args.import_approved:
        import_approved(args.import_approved, args.source_root or cfg['music']['path'])
    elif args.import_annotations:
        db = store.connect()
        try:
            for row in json.loads(Path(args.import_annotations).read_text()):
                if not isinstance(row.get('ref'),str) or not 0 <= float(row['start']) < float(row['end']) or not row.get('provider'):
                    raise ValueError('Annotation needs ref, provider and valid start/end')
                db.execute('INSERT OR REPLACE INTO hook_lookups VALUES(?,?,?,?,?)', ('annotations',row['ref'],'HIT',json.dumps([row]),0))
        finally:
            db.close()
    elif args.ref and args.path:
        try:
            prepare(args.ref, args.path, cfg)
        except Exception as error:
            # Keep diagnostics out of the audio path and never report a failed worker as NO_HOOK.
            print(type(error).__name__, flush=True)
            raise SystemExit(1)
    else:
        p.error('Provide --ref and --path, or --import-approved')
