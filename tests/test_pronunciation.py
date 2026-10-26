import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, daemon, editor, host
from radio.tts import MockTTS, pronounce


class PronunciationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-pronunciation-')
        self.addCleanup(tmp.cleanup)
        override = patch.object(config, 'DATA_DIR', Path(tmp.name))
        override.start()
        self.addCleanup(override.stop)
        self.cfg = config.load()
        self.cfg['voice']['engine'] = 'qwen3'
        self.ed = editor.Editor(self.cfg)

    def test_omnivoice_uses_ready_phonemes_without_llm(self):
        self.cfg['voice']['engine'] = 'voicestudio'
        with patch.object(self.ed, '_complete') as complete:
            self.assertEqual(self.ed.speech('[laughter] Numb — Daft Punk, Get Lucky.'),
                             '[laughter] [N AH1 M] — [D AE1 F T] [P AH1 NG K], [G EH1 T] [L AH1 K IY0].')
            self.assertEqual(self.ed.speech('Muse — Uprising'), 'Мьюз — Апрайзинг')
            complete.assert_not_called()

    def test_native_phonemes_are_idempotent_and_do_not_change_other_engines(self):
        text = 'Играет Daft Punk, «Get Lucky». [laughter]'
        spoken = pronounce(text, phonemes=True)
        self.assertEqual(pronounce(spoken, phonemes=True), spoken)
        self.assertEqual(pronounce(text), text)

    def test_artist_and_track_use_one_request_and_no_disk_cache(self):
        text = host.music_link(('Linkin Park', 'Numb'), '', {}, 'test')
        answer = json.dumps({'Linkin Park': 'Линкин Парк', 'Numb': 'Нам'})
        before = set(config.DATA_DIR.rglob('*'))
        with patch.object(self.ed, '_complete', return_value=answer) as complete:
            said = self.ed.speech(text)
            self.assertIn('Линкин Парк', said)
            self.assertIn('Нам', said)
            self.assertEqual(self.ed.speech(text), said)
            complete.assert_called_once()
            self.assertEqual(complete.call_args.kwargs['timeout'], 15)
            self.assertTrue(complete.call_args.kwargs['json_mode'])
        self.assertIn('Linkin Park', text)
        self.assertEqual(set(config.DATA_DIR.rglob('*')), before)

    def test_dictionary_wins_and_tags_urls_and_numbers_are_not_sent(self):
        text = '[laughter] GitHub, Claude Code, 2026. https://private.test/secret'
        with patch.object(self.ed, '_complete') as complete:
            self.assertEqual(self.ed.speech(text), '[laughter] Гитхаб, Клод Код, 2026. ')
            complete.assert_not_called()

    def test_unknown_name_is_preserved_and_negative_result_reused(self):
        with patch.object(self.ed, '_complete', return_value='{"Xyzzy": null}') as complete:
            self.assertEqual(self.ed.speech('Играет Xyzzy.'), 'Играет Xyzzy.')
            self.ed.speech('Xyzzy')
            complete.assert_called_once()

    def test_invalid_or_unavailable_reply_keeps_names_and_backs_off(self):
        for reply in ['[]', '{"Band": "Перевод. Инструкция"}', '{"Band": "Band"}',
                      '{"Band": ""}', '{"Other": "Бэнд"}', '{"Band": ["Бэнд"]}', '']:
            with self.subTest(reply=reply):
                ed = editor.Editor(self.cfg)
                with patch.object(ed, '_complete', return_value=reply) as complete:
                    self.assertEqual(ed.speech('Band'), 'Band')
                    self.assertEqual(ed.speech('Band'), 'Band')
                    complete.assert_called_once()
                    self.assertEqual(ed.pronunciations, {})
        with patch.object(self.ed, '_complete', side_effect=OSError('offline')):
            self.assertEqual(self.ed.speech('Band'), 'Band')

    def test_cache_has_a_fixed_bound_and_requests_are_batched(self):
        self.ed.pronunciations = {f'Band{i}': 'Группа' for i in range(256)}
        with patch.object(self.ed, '_complete', return_value='{"Linkin Park": "Линкин Парк", "Numb": "Нам"}'):
            self.assertEqual(self.ed.speech('Linkin Park — Numb'), 'Линкин Парк — Нам')
        self.assertEqual(len(self.ed.pronunciations), 256)
        self.assertNotIn('Band0', self.ed.pronunciations)
        self.assertNotIn('Band1', self.ed.pronunciations)

    def test_disabled_editor_and_exhausted_budget_preserve_original(self):
        self.cfg['editor']['llm_enabled'] = False
        with patch.object(self.ed, '_complete') as complete:
            self.assertEqual(self.ed.speech('Band и GitHub'), 'Band и Гитхаб')
            complete.assert_not_called()
        self.cfg['editor']['llm_enabled'] = True
        self.ed.max_calls = 0
        with patch.object(self.ed, '_post') as post:
            self.assertEqual(self.ed.speech('Band'), 'Band')
            post.assert_not_called()

    def test_json_mode_and_timeout_reach_each_provider(self):
        for provider in ('ollama', 'openai', 'deepseek'):
            with self.subTest(provider=provider):
                self.cfg['editor']['provider'] = provider
                reply = {'choices': [{'message': {'content': '{"Band": "Бэнд"}'}}],
                         'output': [{'type': 'message', 'content': [
                             {'type': 'output_text', 'text': '{"Band": "Бэнд"}'}]}]}
                ed = editor.Editor(self.cfg)
                with patch.object(ed, '_post', return_value=reply) as post:
                    self.assertEqual(ed.speech('Band'), 'Бэнд')
                    body = post.call_args.args[2]
                    if provider == 'openai':
                        self.assertEqual(body['text']['format'], {'type': 'json_object'})
                    else:
                        self.assertEqual(body['response_format'], {'type': 'json_object'})
                    self.assertEqual(post.call_args.kwargs['timeout'], 15)

    def test_malformed_provider_reply_cannot_break_speech(self):
        with patch.object(self.ed, '_post', return_value={'choices': []}):
            self.assertEqual(self.ed.speech('Band'), 'Band')

    def test_both_hosts_receive_spelling_but_original_is_preserved(self):
        svc = daemon.Service(self.cfg, tts=MockTTS(), output='x')
        self.addCleanup(svc.db.close)
        other = MockTTS()
        text = 'Вы слушали Linkin Park — «Numb».'
        reply = '{"Linkin Park": "Линкин Парк", "Numb": "Нам"}'
        with patch.object(svc.editor, '_complete', return_value=reply) as complete, \
             patch('radio.daemon.cached_synthesize', return_value=Path('audio.wav')) as synth:
            svc._say(text)
            self.assertIs(synth.call_args.args[0], svc.tts)
            self.assertEqual(synth.call_args.args[1], 'Вы слушали Линкин Парк — «Нам».')
            svc._say(text, other)
            self.assertIs(synth.call_args.args[0], other)
            complete.assert_called_once()
        self.assertEqual(text, 'Вы слушали Linkin Park — «Numb».')
