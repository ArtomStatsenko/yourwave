"""Thumbs up: a mark belongs to one airing of a song, not to the song."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, control, store
from radio.timeline import timeline
from tests.ui_page import page_source


class MarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, target in [('DATA_DIR', root), ('RUN_DIR', root / 'run'), ('SOCK', root / 'run/radio.sock')]:
            patcher = patch.object(config, name, target)
            patcher.start(); self.addCleanup(patcher.stop)
        (root / 'db').mkdir()
        self.db = store.connect(); self.addCleanup(self.db.close)
        at = store.iso(store.now())
        for ref, kind in (('/m/song.mp3', 'MUSIC_STARTED'), ('/m/song.mp3', 'MUSIC_STARTED'), ('pkg', 'PACKAGE_STARTED')):
            self.db.execute('INSERT INTO events(at,type,ref) VALUES(?,?,?)', (at, kind, ref))

    def marked(self):
        with patch('radio.timeline.ytmusic.library', return_value={'tracks': []}):
            return {e['id']: e['marked'] for e in timeline() if e['kind'] == 'music'}

    def test_a_mark_is_one_airing_idempotent_and_taken_back(self):
        for _ in range(2):
            result, code = control.execute({'cmd': 'mark', 'id': 1})
            self.assertEqual((code, result), (200, {'ok': True, 'id': 1, 'marked': True}))
        self.assertEqual(self.marked(), {1: True, 2: False}, 'the same song played again starts without the mark')
        self.assertEqual([tuple(r) for r in self.db.execute('SELECT event_id,ref FROM marks')], [(1, '/m/song.mp3')])
        for _ in range(2):
            self.assertEqual(control.execute({'cmd': 'unmark', 'id': 1})[1], 200)
        self.assertEqual(self.marked(), {1: False, 2: False})

    def test_only_an_aired_song_can_be_marked(self):
        for body in ({'cmd': 'mark', 'id': 3}, {'cmd': 'mark', 'id': 99}, {'cmd': 'mark', 'id': 'x'}, {'cmd': 'mark'}):
            with self.subTest(body=body):
                result, code = control.execute(body)
                self.assertEqual(code, 400)
                self.assertFalse(result['ok'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM marks').fetchone()[0], 0)


class MarkViewTests(unittest.TestCase):
    def test_songs_have_a_mark_left_of_play_again_other_rows_none(self):
        page = page_source()
        start = page.index('// a thumbs up for one airing')
        script = page[start:page.index('function backgroundError(', start)]
        script += page[page.index('function trackSource('):page.index('var rerollFx=null;')]
        harness = r'''
const assert=require('node:assert/strict');
function esc(s){return String(s)}var open={},st={state:'PLAYING'},appearance={formatTime:t=>t};
var RadioModel={entryDetails:()=>[]};
const song=row({id:7,kind:'music',time:'13:58',kindLabel:'',text:'Song',replayable:true,marked:true});
assert.match(song,/data-mark="7" aria-pressed="true" aria-label="Thumbs up"/);
assert.ok(song.indexOf('data-mark')<song.indexOf('data-q'),'the thumb sits left of play again');
assert.match(row({id:8,kind:'music',time:'13:59',kindLabel:'',text:'Song',replayable:false}),/data-mark="8" aria-pressed="false"/);
assert.doesNotMatch(row({id:9,kind:'news',time:'14:00',kindLabel:'news',text:'Edition'}),/data-mark/);
'''
        result = subprocess.run(['node', '-e', script + harness], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
