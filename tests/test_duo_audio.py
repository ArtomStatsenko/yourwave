"""Render real audio to verify hard voice switches and ordinary transitions."""
import re
import math
import wave
from array import array
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import audio


class DuoAudioTests(unittest.TestCase):
    def test_back_sell_keeps_its_beginning_before_a_trimmed_jingle(self):
        with tempfile.TemporaryDirectory(prefix='radio-back-sell-') as tmp:
            lead, jingle = Path(tmp) / 'lead.wav', Path(tmp) / 'jingle.wav'
            audio._ffmpeg('-y', '-f', 'lavfi', '-i',
                          r'aevalsrc=0.1*sin(2*PI*if(lt(t\,2)\,440\,880)*t):d=4:s=44100', str(lead))
            audio._ffmpeg('-y', '-f', 'lavfi', '-i', 'sine=frequency=1760:duration=1',
                          '-af', 'adelay=600:all=1', str(jingle))
            jingles = dict(audio.PLACEHOLDER_JINGLES, stinger_in=jingle)
            self.assertGreater(audio.active_span(jingle)[0], .1)
            for talkover in (False, True):
                with self.subTest(talkover=talkover):
                    out = Path(tmp) / 'break.wav'
                    audio.render_package([lead], out, -20, jingles=jingles, lead=lead, talkover=talkover)
                    with wave.open(str(out)) as wav:
                        rate, channels = wav.getframerate(), wav.getnchannels()
                        samples = array('h', wav.readframes(rate // 5))[::channels]
                    def power(hz):
                        return abs(sum(v * complex(math.cos(2 * math.pi * hz * i / rate),
                                                   math.sin(2 * math.pi * hz * i / rate))
                                       for i, v in enumerate(samples)))
                    self.assertGreater(power(440), power(880) * 10)

    def test_speaker_switch_has_no_gap_separator_or_voice_fade(self):
        with tempfile.TemporaryDirectory(prefix='radio-duo-') as tmp:
            voice = Path(tmp) / 'voice.wav'
            audio._ffmpeg('-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                          '-af', 'adelay=200:all=1,apad=pad_dur=0.2', str(voice))
            span = audio.speech_span(voice)
            self.assertGreater(span[0], .15)
            self.assertLess(span[1], 1.25)
            for changes in (None, [False, False], [True, False]):
                with self.subTest(changes=changes), patch.object(audio, '_ffmpeg', wraps=audio._ffmpeg) as ffmpeg:
                    out = Path(tmp) / 'break.wav'
                    audio.render_package([voice] * 3, out, -20, separators=[True, False],
                                         speaker_changes=changes)
                    self.assertGreater(audio.duration(out), 3)
                    args = next(c.args for c in ffmpeg.call_args_list if '-filter_complex' in c.args)
                    graph = args[args.index('-filter_complex') + 1]
                    delays = [int(re.search(r'adelay=(\d+)', part).group(1))
                              for part in graph.split(';') if re.match(r'\[[456]\]atrim=', part)]
                    speech_ms = (span[1] - span[0]) * 1000
                    if changes and changes[0]:
                        self.assertAlmostEqual(delays[1] - delays[0], speech_ms, delta=2)
                        self.assertNotIn('[sd0]', graph)
                    else:
                        self.assertGreater(delays[1] - delays[0], speech_ms + 100)
                        self.assertIn('[sd0]', graph)
                    self.assertAlmostEqual(delays[2] - delays[1], speech_ms + 300, delta=2)
                    voices = [part for part in graph.split(';') if re.match(r'\[[456]\]atrim=', part)]
                    self.assertTrue(all('afade' not in part for part in voices))
