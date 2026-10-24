"""Rebuild the four CC BY 4.0 assets from the official Tech Live MP3 (see assets/RIGHTS.md)."""
import argparse
import hashlib
import json
import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path

SOURCE_SHA256 = '3fee678a805937e736449fe98347bbf3497258f6387b5569e49c97bae4c1072e'
BEAT = 60 / 124
# Filename: start beat, number of beats, end fade seconds.
CUTS = {'stinger_in.wav': (32, 4, .08), 'bed.wav': (32, 32, .005),
        'separator.wav': (60, 2, .08), 'stinger_out.wav': (64, 4, .35)}
ASSETS = Path(__file__).resolve().parents[1] / 'assets'
CREDIT = 'Tech Live by Kevin MacLeod (incompetech.com), CC BY 4.0, https://creativecommons.org/licenses/by/4.0/; edited excerpt, normalized, faded.'


def ffmpeg(*args):
    return subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', *map(str, args)],
                          capture_output=True, text=True, check=True, timeout=120)


def levels(path):
    result = ffmpeg('-i', path, '-af', 'loudnorm=I=-16:TP=-3:LRA=11:print_format=json', '-f', 'null', '-')
    data, _ = json.JSONDecoder().raw_decode(result.stderr[result.stderr.rfind('{'):])
    return float(data['input_i']), float(data['input_tp'])


def build(source):
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Source is not the verified official Tech Live MP3; see assets/RIGHTS.md')
    entries = []
    with tempfile.TemporaryDirectory(prefix='.base-audio-', dir=ASSETS) as tmp:
        folder = Path(tmp)
        for name, (start, beats, fade) in CUTS.items():
            raw, out = folder / 'raw.wav', folder / name
            duration = beats * BEAT
            ffmpeg('-ss', f'{start * BEAT:.9f}', '-i', source, '-t', f'{duration:.9f}',
                   '-ar', 44100, '-ac', 2, '-c:a', 'pcm_s16le', raw)
            loudness, _ = levels(raw)
            if not math.isfinite(loudness):
                raise ValueError(f'Silent source fragment: {name}')
            filters = (f'volume={-16-loudness:.3f}dB,alimiter=limit=0.707946:level=false:latency=true,'
                       f'afade=t=in:d=0.005,afade=t=out:st={duration-fade:.9f}:d={fade}')
            ffmpeg('-i', raw, '-af', filters, '-metadata', f'title=Tech Live - {Path(name).stem}',
                   '-metadata', 'artist=Kevin MacLeod', '-metadata', 'copyright=CC BY 4.0',
                   '-metadata', f'comment={CREDIT}', '-c:a', 'pcm_s16le', out)
            loudness, peak = levels(out)
            with wave.open(str(out)) as wav:
                frames = wav.getnframes()
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 2, 44100)
                assert abs(frames / 44100 - duration) < .002, name
            assert math.isfinite(loudness) and -24 <= loudness <= -11, (name, loudness)
            assert math.isfinite(peak) and peak < -.1, (name, peak)
            entries.append({'path': 'assets/' + name, 'sha256': hashlib.sha256(out.read_bytes()).hexdigest(),
                            'bytes': out.stat().st_size, 'duration_seconds': frames / 44100,
                            'source_start_seconds': start * BEAT, 'source_beats': beats,
                            'fade_in_seconds': .005, 'fade_out_seconds': fade, 'lufs': loudness, 'true_peak_dbtp': peak})
        for name in CUTS:
            os.replace(folder / name, ASSETS / name)
    return entries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='official Tech Live.mp3, SHA-256 checked before writing assets')
    print(json.dumps(build(parser.parse_args().source), indent=2))
