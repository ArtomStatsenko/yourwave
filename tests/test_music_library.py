import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from radio import config, music, store
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class MusicLibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-library-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root / 'run'),
                            ('SOCK', self.root / 'run/radio.sock')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.svc = Service(config.load(), tts=MockTTS(), output='x')
        self.addCleanup(self.svc.db.close)

    def test_quoted_path_survives_scan_playlist_choices_resume_and_replay(self):
        folder = self.root / 'Музыка "live"'
        folder.mkdir()
        song = folder / 'Song "quoted" \\ mix.wav'
        song.touch()
        files = music.scan(folder)
        self.assertEqual(files, [song])
        playlist = self.root / 'music.m3u'
        rooms = music.write_m3u(self.svc.db, files, playlist, -16, 4)
        self.assertIn(str(song), rooms)
        line = playlist.read_text().splitlines()[1]
        quoted = json.dumps(str(song), ensure_ascii=False)
        self.assertIn('ref=' + quoted + ',', line)
        engine = Liquidsoap(playlist, 3)
        self.assertEqual(engine.music_choices(str(song)), {str(song): line})
        self.svc.engine = engine
        self.svc.resume_offset = {str(song): 12}
        self.assertEqual(self.svc._resume_line(), line.replace('annotate:', 'annotate:liq_cue_in="12.00",', 1))
        engine.command = AsyncMock(return_value='42')
        self.assertEqual(asyncio.run(engine.push_replay(song, 'music', str(song), 0, 'r1')), '42')
        self.assertIn('ref=' + quoted + ',', engine.command.call_args.args[0])
        self.assertTrue(engine.command.call_args.args[0].endswith(':' + str(song)))

    def test_bad_annotation_does_not_hide_the_remaining_choices(self):
        playlist = self.root / 'music.m3u'
        valid = 'annotate:kind="music",ref="ytmusic:abcdefghijk":ytmusic:abcdefghijk'
        playlist.write_text('annotate:ref="bad\\q":/bad.wav\n' + valid + '\n')
        self.assertEqual(Liquidsoap(playlist, 3).music_choices('ytmusic:abcdefghijk'),
                         {'ytmusic:abcdefghijk': valid})

    def test_songs_keep_their_times_while_an_edition_waits_in_the_queue(self):
        import wave
        edition = self.root / 'edition.wav'
        with wave.open(str(edition), 'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b'\0\0' * 8000 * 30)
        at = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)
        self.svc.state = 'PLAYING'
        self.svc.current = {'kind': 'music', 'started': at.timestamp(), 'duration': 180}
        rows = [{'kind': 'news', 'ref': 'p1', 'uri': f'annotate:plan_queue="news",kind="news",ref="p1",liq_fade_in="0.1":{edition}', 'duration': 0},
                {'kind': 'music', 'ref': 'one', 'duration': 180}]
        with patch('radio.daemon.time.time', return_value=at.timestamp()):
            songs = self.svc._music_plan_rows(rows)
        self.assertEqual(songs[0]['at'], store.iso(at + timedelta(seconds=210)), 'after the song on air and the 30 s edition')

    def test_music_estimates_shift_future_editions_at_song_boundaries(self):
        at = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)
        self.svc.state = 'PLAYING'
        self.svc.current = {'kind': 'music', 'started': at.timestamp(), 'duration': 180}
        plan = [{'id': 'edition', 'kind': 'news', 'at': store.iso(at), 'duration': 45, 'status': 'PENDING'},
                {'id': 'later', 'kind': 'news', 'at': store.iso(at + timedelta(minutes=5)),
                 'duration': 120, 'status': 'PENDING'}]
        rows = [{'kind': 'music', 'ref': ref, 'duration': 180} for ref in ('one', 'two')]
        with patch('radio.daemon.time.time', return_value=at.timestamp()):
            songs = self.svc._music_plan_rows(rows, plan=plan)
        self.assertEqual(songs[0]['at'], store.iso(at + timedelta(seconds=225)))
        self.assertEqual(plan[0]['expected_at'], store.iso(at + timedelta(seconds=180)))
        self.assertEqual(plan[1]['expected_at'], store.iso(at + timedelta(seconds=405)))
        self.assertEqual(songs[1]['at'], store.iso(at + timedelta(seconds=525)))

    def test_queued_skipped_and_expired_blocks_do_not_add_a_second_delay(self):
        at = datetime(2026, 10, 26, 9, 30, tzinfo=timezone.utc)
        self.svc.state = 'PLAYING'
        self.svc.current = {'kind': 'music', 'started': at.timestamp(), 'duration': 180}
        for extra in ({'status': 'QUEUED'}, {'skipped': True}, {'ends': store.iso(at + timedelta(seconds=60))}):
            with self.subTest(extra=extra):
                plan = [{'kind': 'news', 'at': store.iso(at), 'duration': 45, 'status': 'PENDING', **extra}]
                rows = [{'kind': 'news', 'duration': 45}, {'kind': 'music', 'ref': 'one', 'duration': 180}]
                with patch('radio.daemon.time.time', return_value=at.timestamp()):
                    songs = self.svc._music_plan_rows(rows, plan=plan)
                self.assertEqual(songs[0]['at'], store.iso(at + timedelta(seconds=225)))

    def test_deleted_files_are_pruned_and_new_track_selection_keeps_working(self):
        gone, kept = self.root / 'gone.wav', self.root / 'kept.wav'
        for song in (gone, kept):
            song.touch()
        self.svc.files = [gone, kept]
        self.svc.intro_rooms = {str(gone): None, str(kept): None}
        self.svc.cfg['broadcast']['new_min_tracks'] = 1
        gone.unlink()
        with patch('radio.daemon.audio.duration', return_value=180):
            self.assertEqual(self.svc._pick_new(), [kept])
        self.assertEqual(self.svc.files, [kept])
        # A file can disappear after pruning, too.
        kept.unlink()
        self.assertEqual(music.unaired_new(self.svc.db, [kept], 14), [])

    def test_a_song_added_to_the_folder_joins_once_copied_and_measured(self):
        folder = self.root / 'music'
        folder.mkdir()
        (config.RUN_DIR).mkdir(parents=True, exist_ok=True)
        (config.RUN_DIR / 'ytmusic.m3u').write_text('#EXTM3U\n')  # the daemon writes it at start
        old = folder / 'old.wav'
        old.touch()
        self.svc.cfg['music']['path'] = folder
        self.svc.files, self.svc.library, self.svc.intro_rooms = [old], {str(old)}, {str(old): None}
        self.svc.engine = Liquidsoap(config.RUN_DIR / 'ytmusic.m3u', 3, 'x', fallback_m3u=config.RUN_DIR / 'music.m3u')
        self.svc.engine.add_music = AsyncMock(return_value='OK')
        def measure(db, files, stop):
            for f in files:
                db.execute('INSERT INTO tracks(path, sig, lufs, ok, tail_sec) VALUES(?,?,-16,1,0.5)', (str(f), music._sig(f)))
        seen, folders = {}, {}
        tick = lambda: asyncio.run(self.svc._library_tick(seen, folders))
        with patch('radio.daemon.music.measure_missing', side_effect=measure) as measured, \
             patch('radio.daemon.music.scan', wraps=music.scan) as scanned, \
             patch.object(self.svc, '_new_set_due', return_value=False):
            tick()
            new = folder / 'new.wav'
            new.write_bytes(b'1')
            tick()
            self.svc.engine.add_music.assert_not_awaited()  # it may still be copying
            tick()
            measured.assert_called_once_with(unittest.mock.ANY, [new], self.svc.stop_measure)
            self.svc.engine.add_music.assert_awaited_once()
            playlist, line = self.svc.engine.add_music.await_args.args
            self.assertEqual((playlist, self.svc.engine.add_music.await_args.kwargs), ('local_music', {'now': True}))
            self.assertIn(f'ref="{new}"', line)
            self.assertEqual((self.svc.files, str(new) in self.svc.library, str(new) in self.svc.intro_rooms), ([new, old], True, True))
            looks = scanned.call_count
            tick()
            self.assertEqual(scanned.call_count, looks, 'nothing changed: only the folder times are read')
        with patch('radio.daemon.music.measure_missing', side_effect=measure), patch.object(self.svc, '_new_set_due', return_value=True):
            (folder / 'next.wav').write_bytes(b'1')
            tick(); tick()
            self.assertEqual(self.svc.engine.add_music.await_args.kwargs, {'now': False}, 'Unplayed tracks first plays it next')

    def test_missing_measured_file_does_not_stop_the_rest_of_the_library(self):
        gone, kept = self.root / 'gone.wav', self.root / 'kept.wav'
        for song in (gone, kept):
            song.touch()
            self.svc.db.execute('INSERT INTO tracks(path, sig) VALUES(?, ?)', (str(song), music._sig(song)))
        gone.unlink()
        with patch('radio.music.audio.tags', return_value=('Artist', 'Song')) as tags, \
                patch('radio.music.audio.lufs', return_value=None), patch('radio.music.find_vocals'):
            music.measure_missing(self.svc.db, [gone, kept], threading.Event())
        tags.assert_called_once_with(kept)
        playlist = self.root / 'music.m3u'
        self.assertEqual(music.write_m3u(self.svc.db, [gone, kept], playlist, -16, 4), {})
        self.assertEqual(playlist.read_text(), '#EXTM3U\n\n')
