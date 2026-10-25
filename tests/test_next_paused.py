import asyncio
import unittest

import test_direction


class NextPausedTests(unittest.TestCase):
    def setUp(self):
        self.editor = test_direction.DirectionTests()
        self.addCleanup(self.editor.doCleanups)
        self.editor.setUp()
        self.editor.next_fixture()

    def test_paused_radio_rejects_next_without_touching_audio(self):
        for automatic in (True, False):
            with self.subTest(auto_paused=automatic):
                self.editor.svc.state = 'PAUSED'
                self.editor.svc.auto_paused = automatic
                current = self.editor.svc.current
                result = asyncio.run(self.editor.svc.dispatch('next'))
                self.assertFalse(result['ok'])
                self.assertIn('paused', result['error'])
                self.editor.svc.engine.command.assert_not_awaited()
                self.editor.svc.engine.wait_music.assert_not_awaited()
                self.assertIs(self.editor.svc.current, current)
                self.assertEqual(self.editor.svc.state, 'PAUSED')

    def test_playing_radio_still_accepts_next(self):
        self.editor.svc.state = 'PLAYING'
        result = asyncio.run(self.editor.svc.dispatch('next'))
        self.assertTrue(result['ok'])
        self.editor.svc.engine.command.assert_awaited_once_with('skip')
