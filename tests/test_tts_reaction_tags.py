import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config
from radio.tts import MlxClone, VoiceStudio, cached_synthesize


class ReactionTagTests(unittest.TestCase):
    def test_clone_strips_reaction_tags_before_synthesis_and_cache_lookup(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            cache = Path(folder) / 'cache/tts'
            cache.mkdir(parents=True)
            ref = Path(folder) / 'host'
            ref.with_suffix('.wav').write_bytes(b'reference')
            ref.with_suffix('.txt').write_text('Голос ведущего.')
            voice = MlxClone('http://unused', 'qwen3', ref)
            text = '[laughter] Продолжаем. [surprise-ah] Да. [sigh]'
            (cache / f'{voice.cache_key(text)}.wav').write_bytes(b'old tagged audio')
            with patch.object(voice, 'synthesize', side_effect=lambda text, out: out.write_bytes(b'clean')) as say:
                out = cached_synthesize(voice, text)
                say.assert_called_once_with('Продолжаем. Да.', out)
                self.assertEqual(out.read_bytes(), b'clean')
                self.assertEqual(cached_synthesize(voice, 'Продолжаем. Да.'), out)
                self.assertEqual(say.call_count, 1)
            with self.assertRaises(ValueError):
                cached_synthesize(voice, '[laughter]')

    def test_omnivoice_keeps_native_reactions(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            (Path(folder) / 'cache/tts').mkdir(parents=True)
            voice = VoiceStudio('http://unused', 'host', 'ru')
            text = '[laughter] Продолжаем. [surprise-ah] Да.'
            with patch.object(voice, 'synthesize', side_effect=lambda text, out: out.write_bytes(b'reaction')) as say:
                out = cached_synthesize(voice, text)
                say.assert_called_once_with(text, out)
