"""Render real audio to verify hard voice switches and ordinary transitions."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import audio


class DuoAudioTests(unittest.TestCase):
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
