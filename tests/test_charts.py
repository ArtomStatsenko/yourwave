"""Charts: songs ranked by thumbs up over a rolling window, with the move since yesterday."""
import json
import subprocess
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from radio import charts, config, store
from tests.ui_page import WEB, theme_html


class ChartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, 'DATA_DIR', Path(self.temp.name))
        patcher.start(); self.addCleanup(patcher.stop)
        (Path(self.temp.name) / 'db').mkdir()
        self.db = store.connect(); self.addCleanup(self.db.close)
        self.now = store.now()

    def mark(self, ref, days_ago, plays=1):
        at = store.iso(self.now - timedelta(days=days_ago))
        for _ in range(plays):
            event = self.db.execute("INSERT INTO events(at,type,ref) VALUES(?,'MUSIC_STARTED',?)", (at, ref)).lastrowid
        self.db.execute('INSERT INTO marks(event_id,ref,at) VALUES(?,?,?)', (event, ref, at))

    def test_most_marks_first_then_by_title_per_window_with_yesterdays_place(self):
        self.mark('/m/Bonobo - Kerala.mp3', 0.1)
        self.mark('/m/Bonobo - Kerala.mp3', 0.2)
        self.mark('/m/Air - La femme.mp3', 0.1, plays=3)
        self.mark('/m/Cat - Song.mp3', 2)
        self.mark('/m/Cat - Song.mp3', 3)
        self.mark('/m/Cat - Song.mp3', 4)
        self.mark('/m/Old - Tune.mp3', 20)
        self.mark('/m/Zed - First.mp3', 2)
        with patch('radio.charts.music.label', side_effect=lambda db, ref: Path(ref).stem):
            data = charts.listing(self.db, self.now)
        week = [(r['place'], r['title'], r['marks'], r['plays'], r['was']) for r in data['week']]
        self.assertEqual(week, [(1, 'Cat - Song', 3, 3, 1), (2, 'Bonobo - Kerala', 2, 2, None),
                                (3, 'Air - La femme', 1, 3, None), (4, 'Zed - First', 1, 1, 2)])
        self.assertEqual([r['title'] for r in data['month']][-1], 'Zed - First', 'ties go by title')
        self.assertIn('Old - Tune', [r['title'] for r in data['all']])
        self.assertNotIn('Old - Tune', [r['title'] for r in data['week']])

    def test_no_marks_no_rows(self):
        self.assertEqual(charts.listing(self.db, self.now), {'week': [], 'month': [], 'all': []})


class ChartModelTests(unittest.TestCase):
    def test_the_model_says_the_plays_marks_share_and_move(self):
        data = {'week': [{'place': 1, 'ref': 'a', 'title': 'A', 'marks': 4, 'plays': 1, 'was': 3},
                         {'place': 2, 'ref': 'b', 'title': 'B', 'marks': 1, 'plays': 2, 'was': 1},
                         {'place': 3, 'ref': 'c', 'title': 'C', 'marks': 1, 'plays': 1, 'was': 3},
                         {'place': 4, 'ref': 'd', 'title': 'D', 'marks': 1, 'plays': 1, 'was': None}]}
        script = (WEB / 'core/core.js').read_text() + (WEB / 'core/model.js').read_text() + r'''
const assert=require('node:assert/strict'),c=RadioModel.charts(''' + json.dumps(data) + r''','week');
assert.deepEqual(c.windows.map(w=>[w.label,w.selected]),[['Last 7 days',true],['Last 30 days',false],['All time',false]]);
assert.equal(c.note,'updates as you mark');assert.equal(c.empty,'');
assert.deepEqual(c.rows.map(r=>r.move.text),['▲ 2','▼ 1','–','new']);
assert.equal(c.rows[0].detail,'1 play · 4 times marked');assert.equal(c.rows[1].detail,'2 plays · 1 time marked');
assert.deepEqual(c.rows.map(r=>r.share),[100,25,25,25]);assert.equal(c.rows[0].move.label,'Up 2 since yesterday');
assert.notEqual(RadioModel.charts({},'month').empty,'');
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_theme_has_the_charts_tab_after_experiments(self):
        for name in ['terminal', 'lime', 'soft', 'prism', 'cyber']:
            with self.subTest(theme=name):
                html = theme_html(name)
                self.assertLess(html.index('id="tab-ex"'), html.index('id="tab-ch"'))
                self.assertLess(html.index('id="tab-ch"'), html.index('id="tab-st"'))
                self.assertIn('id="charts" role="tabpanel" aria-labelledby="tab-ch"', html)


if __name__ == '__main__':
    unittest.main()
