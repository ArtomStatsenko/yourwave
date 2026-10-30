import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config
from radio.tts import MlxClone, VoiceStudio, cached_synthesize


class ReactionTagTests(unittest.TestCase):
    def test_omnivoice_sends_the_same_seed_for_every_take_of_each_host(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            (Path(folder) / 'cache/tts').mkdir(parents=True)
            requests = []

            def respond(request, timeout):
                requests.append(json.loads(request.data))
                return io.BytesIO(b'speech')

            with patch('radio.tts.urllib.request.urlopen', side_effect=respond):
                for seed in (42, 7):
                    voice = VoiceStudio('http://unused', 'default', 'ru', seed=seed)
                    cached_synthesize(voice, 'Новость, продолжаем.', lambda path: 'noise' if len(requests) % 3 else None)
            self.assertEqual([request['seed'] for request in requests], [42, 42, 42, 7, 7, 7])

    def test_omnivoice_does_not_reuse_legacy_seed_retry_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'DATA_DIR', Path(folder)):
            cache = Path(folder) / 'cache/tts'
            cache.mkdir(parents=True)
            voice = VoiceStudio('http://unused', 'default', 'ru', seed=42)
            text = 'Продолжаем.'
            legacy = f'voicestudio|{voice.voice_id}|{voice.language}|{voice.speed}|{voice.instruct}|{voice.seed}|{text}'
            old = cache / (hashlib.sha256(legacy.encode()).hexdigest()[:32] + '.wav')
            old.write_bytes(b'wrong voice')
            with patch.object(voice, 'synthesize', side_effect=lambda text, out: out.write_bytes(b'fixed voice')) as say:
                out = cached_synthesize(voice, text, lambda path: None)
                say.assert_called_once()
                self.assertNotEqual(out, old)
                self.assertEqual(out.read_bytes(), b'fixed voice')
                self.assertEqual(cached_synthesize(voice, text, lambda path: None), out)
                self.assertEqual(say.call_count, 1)

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
