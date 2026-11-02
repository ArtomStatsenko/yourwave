"""The chart set on air: the week's top songs counted down, each after a host line, as a plan block."""
import asyncio
import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from tests import test_radio  # noqa: F401  (an isolated data folder)
from radio import chartset, config, plugins, store
from radio.daemon import Service
from radio.editor import Editor
from radio.timeline import timeline
from radio.tts import MockTTS
from tests.ui_page import WEB


class ChartSetSettingsTests(unittest.TestCase):
    def setUp(self):
        chartset._path().unlink(missing_ok=True)
        self.addCleanup(chartset._path().unlink, missing_ok=True)

    def test_saved_and_checked(self):
        self.assertEqual(chartset.load(), {'size': 5, 'weekly': False, 'day': 'fri', 'time': '18:00'})
        self.assertEqual(chartset.save({'size': 10, 'weekly': True})['size'], 10)
        self.assertTrue(chartset.load()['weekly'])
        for bad in ({'size': 4}, {'day': 'friday'}, {'time': '25:00'}, {'time': '8:00'}, {'weekly': 'yes'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                chartset.save(bad)
        self.assertEqual(chartset.load()['size'], 10, 'a refused change keeps the saved one')

    def test_the_next_weekly_set_is_in_the_station_zone(self):
        settings = {'size': 5, 'weekly': True, 'day': 'fri', 'time': '18:00'}
        wednesday = datetime(2026, 11, 4, 3, 0, tzinfo=timezone.utc)  # 10:00 in Ho Chi Minh City
        when = chartset.next_weekly(settings, wednesday, 'Asia/Ho_Chi_Minh')
        self.assertEqual((when.weekday(), when.hour, when.minute, when.day), (4, 18, 0, 6))
        friday_late = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)  # 19:00 there: the next Friday
        self.assertEqual(chartset.next_weekly(settings, friday_late, 'Asia/Ho_Chi_Minh').day, 13)
        self.assertIsNone(chartset.next_weekly({**settings, 'weekly': False}, wednesday, 'Asia/Ho_Chi_Minh'))

    def test_the_host_words_have_the_shape_or_the_template_speaks(self):
        songs = [{'place': 2, 'ref': 'b', 'title': 'B - Two'}, {'place': 1, 'ref': 'a', 'title': 'A - One'}]
        ru = chartset.template('Yourwave', 2, 4, songs, 'ru')
        self.assertEqual(ru['lines'], ['Номер 2: B - Two.', 'Номер 1: A - One.'])
        self.assertIn('Пятница', ru['intro'])
        self.assertIn('Friday', chartset.template('Yourwave', 2, 4, songs, 'en')['intro'])
        editor = Editor(config.load())
        with patch.object(Editor, 'enabled', True), patch.object(editor, '_complete',
                return_value=json.dumps({'intro': 'Пятница!', 'lines': ['Номер два.', 'Номер один.']})):
            self.assertEqual(editor.chart_set('Yourwave', 2, 'пятница', songs, 'ru'), {'intro': 'Пятница!', 'lines': ['Номер два.', 'Номер один.']})
        for wrong in ({'intro': 'x', 'lines': ['one']}, {'lines': ['a', 'b']}, 'not json'):
            with patch.object(Editor, 'enabled', True), patch.object(editor, '_complete', return_value=wrong if isinstance(wrong, str) else json.dumps(wrong)):
                self.assertIsNone(editor.chart_set('Yourwave', 2, 'пятница', songs, 'ru'))


class ChartSetOnAirTests(unittest.TestCase):
    def setUp(self):
        self.clock = datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc)
        patcher = patch('radio.daemon.now', return_value=self.clock)
        patcher.start(); self.addCleanup(patcher.stop)
        self.svc = Service(config.load(), tts=MockTTS(), output='x')
        for table in ('events', 'air_plan', 'marks'):
            self.svc.db.execute(f'DELETE FROM {table}')
        plugins.set_plugin('air_direction', True)
        self.addCleanup(plugins.set_plugin, 'air_direction', False)
        chartset._path().unlink(missing_ok=True)
        self.addCleanup(chartset._path().unlink, missing_ok=True)
        at = store.iso(store.now() - timedelta(hours=1))
        for ref, marks in (('/m/A - One.mp3', 3), ('/m/B - Two.mp3', 2), ('/m/C - Three.mp3', 1)):
            for _ in range(marks):
                event = self.svc.db.execute("INSERT INTO events(at,type,ref) VALUES(?,'MUSIC_STARTED',?)", (at, ref)).lastrowid
                self.svc.db.execute('INSERT INTO marks(event_id,ref,at) VALUES(?,?,?)', (event, ref, at))

    def test_play_queues_lines_and_songs_counted_down_and_the_timeline_shows_it(self):
        svc = self.svc
        pushed = []
        svc.engine = Mock(push_replay=AsyncMock(side_effect=lambda wav, kind, ref, gain, rid: pushed.append((kind, ref)) or str(len(pushed))))
        svc._say = Mock(side_effect=lambda text: Path('/tmp/line.wav'))
        answer = asyncio.run(svc.dispatch('chart_set', {'size': 3}))
        self.assertTrue(answer['ok'], answer)
        key = answer['id']
        self.assertEqual(svc.db.execute('SELECT kind,ref,text FROM air_plan WHERE id=?', (key,)).fetchone()[:], ('chart', '3', 'Top 3 of the week'))
        with patch.object(Editor, 'enabled', False):
            self.assertTrue(asyncio.run(svc._direction_tick()))
        self.assertEqual(pushed, [('chart', key + '#0'), ('music', '/m/C - Three.mp3'), ('chart', key + '#1'), ('music', '/m/B - Two.mp3'),
                                  ('chart', key + '#2'), ('music', '/m/A - One.mp3')], 'a line before each song, from the last place')
        row = svc.db.execute('SELECT status,rid FROM air_plan WHERE id=?', (key,)).fetchone()
        self.assertEqual((row['status'], row['rid']), ('QUEUED', '1,2,3,4,5,6'), 'every request of the set, to cancel them together')
        svc.on_event({'kind': 'chart', 'ref': key + '#0', 'replay_id': key})
        self.assertEqual(svc._title(), 'Top 3 of the week')
        with patch('radio.timeline.ytmusic.library', return_value={'tracks': []}):
            entry = next(e for e in timeline() if e['kind'] == 'chart')
        self.assertEqual(entry['title'], 'Top 3 of the week')
        self.assertIn('Номер 3', entry['parts'][0]['text'])
        self.assertEqual(entry['parts'][0]['description'], '3. C - Three · 2. B - Two · 1. A - One')

    def test_needs_air_direction_and_a_marked_song(self):
        plugins.set_plugin('air_direction', False)
        self.assertEqual(asyncio.run(self.svc.dispatch('chart_set', {'size': 5}))['error'], 'Needs Air direction in Experiments')
        plugins.set_plugin('air_direction', True)
        self.assertFalse(asyncio.run(self.svc.dispatch('chart_set', {'size': 4}))['ok'])
        self.svc.db.execute('DELETE FROM marks')
        self.assertEqual(asyncio.run(self.svc.dispatch('chart_set', {'size': 5}))['error'], 'No song was marked this week')

    def test_every_week_puts_the_next_set_in_the_plan_a_day_ahead_once(self):
        chartset.save({'weekly': True, 'day': 'sat', 'time': '09:00'})
        self.svc._chart_weekly()
        self.svc._chart_weekly()
        rows = self.svc.db.execute("SELECT id,kind,not_before FROM air_plan WHERE kind='chart'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['id'].startswith('direction:chart:2026-11-07'))
        chartset.save({'day': 'tue'})
        self.svc.db.execute("DELETE FROM air_plan")
        self.svc._chart_weekly()
        self.assertEqual(self.svc.db.execute("SELECT COUNT(*) FROM air_plan").fetchone()[0], 0, 'more than a day away: not yet')


class ChartSetBlockTests(unittest.TestCase):
    def test_the_block_is_shown_disabled_without_air_direction(self):
        script = (WEB / 'core/core.js').read_text() + (WEB / 'core/model.js').read_text() + r'''
const assert=require('node:assert/strict');
const off=RadioModel.chartSet({size:10,weekly:true,day:'fri',time:'18:00'},false),on=RadioModel.chartSet(null,true);
assert.equal(off.disabled,true);assert.equal(off.note,'Needs Air direction in Experiments');
assert.equal(off.caption,'The host introduces it and counts down from 10');
assert.equal(on.disabled,false);assert.equal(on.note,'');assert.deepEqual(on.sizes.map(s=>s.label),['Top 3','Top 5','Top 10']);
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
