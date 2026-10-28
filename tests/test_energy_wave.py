import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from radio import config, energy, music, plugins, settings, store
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS
from tests.ui_page import page_source


class EnergyWaveTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-energy-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root/'run'), ('SOCK', self.root/'run/radio.sock')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.svc = Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)
        self.files = [self.root/f'{i}.wav' for i in range(10)]
        self.rows = {}
        for i, path in enumerate(self.files):
            path.write_bytes(b'fixture')
            self.rows[str(path)] = dict(sig=energy.signature(path), energy=i % 5 + 1, duration=120+i*15,
                                        bpm=100+i, genre='rock', genre_source='tag', timbre=[0]*8, family='sound group 1')
        self.save()
        self.svc.files = self.files[:]
        self.svc.intro_rooms = dict.fromkeys(map(str, self.files))

    def save(self):
        config.write_json(self.root/'energy.json', dict(version=energy.VERSION, tracks=self.rows))

    def test_default_off_and_experiments_listing_explains_analysis(self):
        self.assertFalse(plugins.on(self.svc.cfg, 'energy_wave'))
        row = next(r for r in plugins.listing(self.svc.cfg) if r['name'] == 'energy_wave')
        self.assertEqual(row['section'], 'experiments')
        self.assertIn('10 tracks', row['description'])
        plugins.set_plugin('energy_wave', True)
        self.assertTrue(plugins.on(self.svc.cfg, 'energy_wave'))

    def test_no_candidate_lost_or_repeated_and_input_order_does_not_change_selection(self):
        ordered = energy.order(self.files, self.rows)
        self.assertCountEqual(ordered, self.files)
        self.assertEqual(len(set(ordered)), len(self.files))
        self.assertEqual(energy.order(self.files[::-1], self.rows), ordered)

    def test_batch_sizes_do_not_change_the_wave_with_mixed_track_durations(self):
        elapsed, previous = 370.0, {'energy': 2, 'genre': 'rock'}
        expected = energy.order(self.files, self.rows, elapsed, previous, until_voice=1100)
        for size in (1, 2, 3, 7):
            pending, actual, seconds, before, voice = self.files[:], [], elapsed, previous, 1100
            while pending:
                batch = energy.order(pending, self.rows, seconds, before, until_voice=voice)[:size]
                for path in batch:
                    pending.remove(path)
                    actual.append(path)
                    before = self.rows[str(path)]
                    seconds += energy.duration(before)
                    voice -= energy.duration(before)
            self.assertEqual(actual, expected)

    def test_continuous_curve_visits_four_and_voice_eases_the_target(self):
        self.assertEqual(energy.target(0), 1)
        self.assertEqual(energy.target(energy.PERIOD_SEC/2), 5)
        self.assertAlmostEqual(energy.target(energy.PERIOD_SEC/3), 4)
        self.assertAlmostEqual(energy.target(energy.PERIOD_SEC), 1)
        self.assertLess(energy.target(900, 60), energy.target(900, 300))
        self.assertLessEqual(energy.target(900, 0), 1.5)

    def test_all_five_levels_are_selected_including_four(self):
        picks = [self.rows[str(energy.order(self.files, self.rows, seconds)[0])]['energy']
                 for seconds in range(0, energy.PERIOD_SEC, 13)]
        self.assertEqual(set(picks), {1, 2, 3, 4, 5})

    def test_recency_softly_prefers_long_unplayed_tracks(self):
        recent, older = self.files[:2]
        rows = {str(p): {'energy': 2, 'duration': 180} for p in (recent, older)}
        count = sum(energy.order([recent, older], rows, t, recency={str(recent): 1})[0] == older for t in range(500))
        self.assertGreater(count, 270)

    def test_planning_projects_music_to_next_boundary_and_accounts_for_timed_voice(self):
        svc = self.svc
        svc.state, svc.energy_seconds = 'PLAYING', 120
        svc.current = {'kind': 'music', 'ref': str(self.files[0]), 'duration': 300, 'started': 1000}
        svc.energy_previous = str(self.files[0])
        due = datetime.fromtimestamp(1350, timezone.utc)
        reminder = {'start': due + timedelta(minutes=svc.cfg['reminders']['lead_min'])}
        with patch('radio.daemon.time.time', return_value=1040), \
             patch.object(svc, '_clock_rows', return_value=[]), \
             patch.object(svc, '_reminder_candidates', return_value=[reminder]), \
             patch('radio.daemon.energy.order', return_value=self.files) as ordered:
            svc._energy_order(self.files)
        self.assertEqual(ordered.call_args.args[2], 420)
        self.assertEqual(ordered.call_args.args[-1], 50)
        self.assertEqual(ordered.call_args.args[3]['energy'], 1)

    def test_genre_is_preferred_at_equal_energy_and_missing_tags_still_work(self):
        same, other = self.files[:2]
        rows = {
                str(same): {'energy': 2, 'genre': 'jazz'},
                str(other): {'energy': 2, 'genre': 'metal'}}
        def preferred(before):
            return sum(energy.order([other, same], rows, t, before)[0] == same for t in range(500))
        self.assertGreater(preferred({'energy': 2, 'genre': 'jazz'}), 280)
        rows[str(same)] = {'energy': 2, 'family': 'warm'}
        rows[str(other)] = {'energy': 2, 'family': 'bright'}
        self.assertGreater(preferred({'energy': 2, 'family': 'warm'}), 280)
        self.assertCountEqual(energy.order(self.files, {}), self.files)

    def test_production_prompt_tags_become_coarse_genres(self):
        self.assertEqual(energy.genre_family('Trap rap; dry close-mic voice, acid synths, distorted metal guitars'), 'hip-hop')
        self.assertEqual(energy.genre_family('Indie Rock, 120 BPM, warm analog synth'), 'rock')
        self.assertEqual(energy.genre_family('orchestral symphony'), 'classical')
        self.assertEqual(energy.genre_family('Русский рэп'), 'hip-hop')
        self.assertEqual(energy.genre_family(''), '')
        self.assertEqual(energy.genre_family('unclassified'), '')

    def test_changed_deleted_and_invalid_cache_fall_back(self):
        self.files[0].write_bytes(b'changed')
        self.files[1].unlink()
        self.assertEqual(len(energy.profiles(self.files)), 8)
        (self.root/'energy.json').write_text('{broken')
        self.assertEqual(energy.profiles(self.files), {})
        self.assertEqual(self.svc._pick_wave(), [])

    def test_manual_calibration_works_in_the_radio_python_without_analysis_dependencies(self):
        env = dict(os.environ, RADIO_DATA_DIR=str(self.root))
        result = subprocess.run([sys.executable, '-m', 'radio.energy', 'set', '--path', str(self.files[0]),
                                 '--energy', '4', '--genre', 'Indie Rock'], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = energy.profiles(self.files)[str(self.files[0])]
        self.assertEqual((row['energy'], row['energy_override']), (4, 4))
        self.assertEqual((row['genre'], row['genre_override'], row['genre_source']), ('indie rock', 'indie rock', 'manual'))
        report = subprocess.run([sys.executable, '-m', 'radio.energy', 'report'], env=env, capture_output=True, text=True)
        self.assertEqual(report.returncode, 0, report.stderr)
        self.assertIn('indie rock', report.stdout)

    def test_rotation_excludes_current_replays_and_actual_plays_until_exhausted(self):
        self.svc.current = {'ref': str(self.files[0])}
        self.svc.replays = {'r': {'ref': str(self.files[1]), 'kind': 'music'}}
        first = self.svc._pick_wave()
        self.assertEqual(len(first), 2)
        self.svc.energy_played.update(map(str, first))
        second = self.svc._pick_wave()
        self.assertFalse(set(first) & set(second))
        self.assertEqual(len(second), 2)
        self.svc.energy_played.update(map(str, self.files))
        third = self.svc._pick_wave()
        self.assertEqual(len(third), 2)
        self.assertNotIn(self.files[0], third)
        self.assertNotIn(self.files[1], third)

    def test_priority_pool_stays_unplayed_and_disabled_order_is_unchanged(self):
        cfg = self.svc.cfg
        with patch('radio.music.unaired_new', return_value=self.files), patch.object(self.svc, '_song_seconds', return_value=240):
            plain = self.svc._pick_new()
            self.assertEqual(plain, self.files[:5])
            plugins.set_plugin('energy_wave', True)
            ordered = self.svc._pick_new()
            self.assertEqual(len(ordered), 2)
            self.assertTrue(all(p in self.files for p in ordered))
        plugins.set_plugin('energy_wave', False)
        self.assertFalse(plugins.on(cfg, 'energy_wave'))

    def test_regular_wave_marks_queue_and_disabling_removes_only_its_requests(self):
        self.svc.engine = AsyncMock()
        self.svc.engine.push_replay.side_effect = ['1', '2']
        plugins.set_plugin('energy_wave', True)

        async def scenario():
            await self.svc._queue_fresh(self.files[:2], wave=True)
            self.svc.fresh_queued['new-track'] = '3'
            await self.svc.dispatch('plugin', {'name': 'energy_wave', 'on': False})
        asyncio.run(scenario())
        self.assertEqual(self.svc.fresh_queued, {'new-track': '3'})
        self.assertFalse(self.svc.energy_queued)
        self.assertEqual({c.args[0] for c in self.svc.engine.remove.await_args_list}, {'1', '2'})
        self.assertNotIn('energy_wave=', self.svc.engine.push_replay.await_args_list[0].args[-2])

    def test_music_clock_excludes_pauses_and_voice_and_preserves_skipped_play_time(self):
        svc = self.svc
        svc.state, svc.energy_seconds = 'PLAYING', 120
        svc.current = {'kind': 'music', 'ref': str(self.files[0]), 'duration': 300, 'started': 1000}
        with patch('radio.daemon.time.time', return_value=1040):
            self.assertEqual(svc._energy_elapsed(), 160)
        svc.state, svc.paused_at = 'PAUSED', 1040
        with patch('radio.daemon.time.time', return_value=1240):
            self.assertEqual(svc._energy_elapsed(), 160)
        svc.state = 'PLAYING'
        with patch('radio.daemon.time.time', return_value=1040), patch.object(svc, '_song_seconds', return_value=180):
            svc.on_event({'kind': 'music', 'ref': str(self.files[1])})
        self.assertEqual(svc.energy_seconds, 160)
        self.assertEqual(svc.energy_previous, str(self.files[1]))
        svc.current = {'kind': 'news', 'started': 1040}
        with patch('radio.daemon.time.time', return_value=1100):
            self.assertEqual(svc._energy_elapsed(), 160)

    def test_restart_preserves_music_clock_without_double_counting_resumed_track(self):
        svc = self.svc
        svc.state, svc.energy_seconds = 'PLAYING', 120
        svc.energy_previous = str(self.files[0])
        svc.current = {'kind': 'music', 'ref': str(self.files[0]), 'duration': 300, 'started': 1000}
        with patch('radio.daemon.time.time', return_value=1040):
            svc._save_session()
            svc.energy_seconds = 0
            self.assertTrue(svc._restore_session())
            self.assertEqual(svc.energy_seconds, 120)
            self.assertEqual(svc.resume_offset[str(self.files[0])], 40)
            self.assertEqual(svc._energy_elapsed(), 160)

    def test_wave_is_queued_only_when_enabled_and_local(self):
        self.svc.state = 'PLAYING'
        self.svc.current = {'kind': 'music', 'ref': str(self.files[0]), 'duration': 300, 'started': time.time()}
        self.svc.engine = AsyncMock()
        with patch.object(self.svc, '_new_set_due', return_value=False), \
             patch.object(self.svc, '_queue_fresh', new_callable=AsyncMock) as queued, \
             patch.object(self.svc, '_clock_slot', return_value=None):
            asyncio.run(self.svc.schedule())
            queued.assert_not_awaited()
            plugins.set_plugin('energy_wave', True)
            self.svc.ytmusic_active = True
            asyncio.run(self.svc.schedule())
            queued.assert_not_awaited()
            self.svc.ytmusic_active = False
            asyncio.run(self.svc.schedule())
            queued.assert_awaited_once()
            self.assertTrue(queued.await_args.kwargs['wave'])

    def test_plan_exposes_ratings_and_ui_escapes_genre_labels(self):
        self.svc.library = set(map(str, self.files))
        plugins.set_plugin('energy_wave', True)
        row = self.svc._music_plan_rows([{'ref': str(self.files[0]), 'kind': 'music', 'duration': 180}])[0]
        self.assertEqual((row['energy'], row['genre'], row['genre_source']), (1, 'rock', 'tag'))
        source = page_source()
        function = source[source.index('  function trackLabels('):source.index('\n  var next=', source.index('  function trackLabels('))]
        harness = '''const assert=require('node:assert/strict');
const tracks=[{ref:'song',energy:3,genre:'<rock>'}],announced=[];
const esc=s=>String(s).replaceAll('<','&lt;').replaceAll('>','&gt;');
FUNCTION
assert(trackLabels({ref:'song'},true).includes('energy 3/5'));
assert(trackLabels({ref:'song'},true).includes('&lt;rock&gt;'));
assert(!trackLabels({ref:'song'},true).includes('<rock>'));
assert.equal(trackLabels({kind:'news',ref:'song'},true),'');
'''
        result = subprocess.run(['node', '-e', harness.replace('FUNCTION', function)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_real_engine_plays_wave_queue_in_order_without_interrupting_current(self):
        for path in self.files:
            with wave.open(str(path), 'wb') as out:
                out.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
                out.writeframes(b'\xe8\x03\xe8\x03' * (44100*3))
            self.rows[str(path)]['sig'] = energy.signature(path)
        self.save()
        self.svc.engine = Liquidsoap(self.root/'music.m3u', .1, str(self.root/'out.wav'))
        music.write_m3u(self.svc.db, self.files, self.root/'music.m3u', -16, 4, .1)
        plugins.set_plugin('energy_wave', True)

        async def scenario():
            try:
                await self.svc.engine.start(self.svc.on_event)
                self.svc.state = 'PLAYING'
                deadline = time.monotonic()+10
                while not self.svc.current and time.monotonic() < deadline:
                    await asyncio.sleep(.1)
                self.assertIsNotNone(self.svc.current)
                current = self.svc.current
                selected = self.svc._pick_wave()
                await self.svc._queue_fresh(selected, wave=True)
                self.assertIs(self.svc.current, current)
                deadline = time.monotonic()+25
                while time.monotonic() < deadline:
                    played = [r[0] for r in self.svc.db.execute("SELECT ref FROM events WHERE type='MUSIC_STARTED' ORDER BY id")]
                    if len(played) >= len(selected)+1:
                        break
                    await asyncio.sleep(.1)
                self.assertEqual(played[1:len(selected)+1], list(map(str, selected)))
                self.assertGreater(self.svc.energy_seconds, 0)
            finally:
                await self.svc.engine.stop()
        with patch.dict('os.environ', RADIO_FILE_SYNC='realtime'):
            asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main()
