import asyncio
import json
import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from radio import config, plugins, previews, store, timeline
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class PreviewTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='radio-preview-');self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        for name,value in [('DATA_DIR',self.root),('RUN_DIR',self.root/'run')]:
            p=patch.object(config,name,value);p.start();self.addCleanup(p.stop)
        self.cfg=config.load();self.svc=Service(self.cfg,tts=MockTTS());self.addCleanup(self.svc.db.close)
        self.svc.state='PLAYING';self.svc.current={'kind':'news','ref':'edition','duration':120,'started':time.time()-30}
        self.svc.engine=AsyncMock();self.svc.engine.command.return_value='OK';self.svc.engine.pending.return_value=[]
        self.snapshot=[{'ref':'one','path':'/one.wav'},{'ref':'two','path':'/two.wav'}]

    def wav(self,name,seconds=4):
        p=self.root/(name+'.wav')
        with wave.open(str(p),'wb') as stream:
            stream.setparams((2,2,44100,0,'NONE','not compressed'));stream.writeframes(b'\xe8\x03\xe8\x03'*int(44100*seconds))
        return p

    def test_switch_is_off_by_default_and_disabled_path_does_nothing(self):
        self.assertFalse(plugins.on(self.cfg,'track_previews'))
        self.assertEqual(next(p for p in plugins.listing(self.cfg) if p['name']=='track_previews')['section'],'experiments')
        asyncio.run(self.svc._preview_tick());self.svc.engine.command.assert_not_awaited()

    def test_only_matching_queue_is_committed_after_news_and_never_between_songs(self):
        plugins.set_plugin('track_previews',True)
        async def command(cmd):
            if cmd.startswith('previews.enable'):return 'OK'
            if cmd in ('previews.prepare','previews.next'):return json.dumps(self.snapshot)
            if cmd.startswith('previews.push'):return '42'
            return ''
        self.svc.engine.command.side_effect=command
        result={'audio_path':str(self.wav('teaser')),'speech_sec':3,'duration_sec':4,'hook_ids':[], 'announced_refs':['one','two'], 'announced_titles':['One','Two']}
        with patch('radio.previews.render',return_value=result):
            asyncio.run(self.svc._preview_tick())
        self.assertEqual(self.svc.preview_job['rid'],'42')
        self.assertEqual(self.svc.status()['announced_tracks'],[])
        self.assertEqual(self.svc.db.execute("SELECT COUNT(*) FROM events WHERE type='MUSIC_TEASER'").fetchone()[0],0)
        self.assertEqual(store.spoken_last_hour(self.svc.db),3)
        job=self.svc.preview_job
        self.svc.on_event({'kind':'teaser','ref':job['id']})
        self.assertEqual(self.svc.status()['announced_tracks'],['one','two'])
        self.assertEqual(self.svc.status()['announced_tracks'],['one','two'],'Refreshing status preserves the announcement')
        self.assertEqual(self.svc.songs_since_break,0)
        self.assertEqual(self.svc.db.execute("SELECT COUNT(*) FROM events WHERE type='MUSIC_STARTED'").fetchone()[0],0)
        rows=timeline._build(self.svc.db,12,10)
        self.assertEqual(rows[0]['title'],'Coming up')
        self.svc.on_event({'kind':'music','ref':'one'})
        self.assertEqual(self.svc.status()['announced_tracks'],['two'])
        self.assertEqual(store.spoken_last_hour(self.svc.db),3)
        self.svc.current={'kind':'music','ref':'one','duration':120,'started':time.time()-30}
        self.svc.preview_retry=0;self.svc.engine.command.reset_mock()
        asyncio.run(self.svc._preview_tick());self.svc.engine.command.assert_not_awaited()

    def test_reroll_removes_only_replaced_announcement_after_success(self):
        self.svc.announced_tracks=['one','two']
        slot={'queue':'music','rid':''}
        self.svc.engine.replace_music.side_effect=ValueError('changed')
        self.assertIsNotNone(asyncio.run(self.svc._swap_next('one','replacement','uri',slot,0)))
        self.assertEqual(self.svc.announced_tracks,['one','two'])
        self.svc.engine.replace_music.side_effect=None
        self.svc.engine.replace_music.return_value='99'
        self.assertIsNone(asyncio.run(self.svc._swap_next('one','replacement','uri',slot,0)))
        self.assertEqual(self.svc.announced_tracks,['two'])
        self.assertNotIn('replacement',self.svc.status()['announced_tracks'])

    def test_voice_fallback_records_only_first_but_validates_reserved_queue(self):
        plugins.set_plugin('track_previews',True);self.svc.preview_enabled=True
        voice=self.wav('voice')
        self.svc.engine.command.side_effect=[json.dumps(self.snapshot),json.dumps(self.snapshot),'42']
        with patch.object(self.svc,'_say',return_value=voice):
            asyncio.run(self.svc._preview_tick())
        job=self.svc.preview_job
        self.assertEqual(json.loads(job['refs']),['one'])
        payload=json.loads(self.svc.engine.command.call_args.args[0].removeprefix('previews.push '))
        self.assertEqual(payload['refs'],['one','two'])
        self.assertIn('preview_refs='+json.dumps('["one","two"]'),payload['uri'])
        self.svc.on_event({'kind':'teaser','ref':job['id']})
        self.assertEqual(self.svc.announced_tracks,['one'])

    def test_source_switch_clears_announcements_only_after_success(self):
        self.svc.announced_tracks=['one','two']
        self.svc.ytmusic_active=True;self.svc.files=[self.wav('local')]
        self.svc.engine.command.return_value='ERROR'
        asyncio.run(self.svc._sync_music_source())
        self.assertEqual(self.svc.announced_tracks,['one','two'])
        self.svc.engine.command.return_value='OK'
        asyncio.run(self.svc._sync_music_source())
        self.assertEqual(self.svc.announced_tracks,[])

    def test_pause_queue_change_and_disable_cancel_pending_preview(self):
        for action in ('pause','changed','disabled','different-edition'):
            with self.subTest(action=action):
                plugins.set_plugin('track_previews',action!='disabled')
                self.svc.preview_enabled=action!='disabled'
                self.svc.preview_job={'id':action,'rid':'42','refs':'["one","two"]','after_news':'edition','snapshot':json.dumps(self.snapshot),'expires':time.time()+60}
                self.svc.db.execute("INSERT INTO music_previews(id,status,speech_sec) VALUES(?,'QUEUED',3)",(action,))
                self.svc.id_pending=True;self.svc.state='PAUSED' if action=='pause' else 'PLAYING'
                self.svc.current={'kind':'news','ref':'different' if action=='different-edition' else 'edition'}
                self.svc.engine.command.return_value='[]' if action=='changed' else json.dumps(self.snapshot)
                asyncio.run(self.svc._preview_tick())
                self.assertIsNone(self.svc.preview_job)
                self.assertEqual(self.svc.db.execute('SELECT status FROM music_previews WHERE id=?',(action,)).fetchone()[0],'CANCELLED')

    def test_change_during_synthesis_does_not_queue_stale_announcement(self):
        plugins.set_plugin('track_previews',True)
        self.svc.preview_enabled=True
        self.svc.engine.command.side_effect=[json.dumps(self.snapshot),json.dumps(self.snapshot[::-1])]
        rendered={'audio_path':str(self.wav('teaser')),'speech_sec':3,'duration_sec':4,'hook_ids':[], 'announced_refs':['one','two'], 'announced_titles':['One','Two']}
        with patch('radio.previews.render',return_value=rendered):
            asyncio.run(self.svc._preview_tick())
        self.assertIsNone(self.svc.preview_job)
        self.assertEqual(self.svc.db.execute('SELECT COUNT(*) FROM music_previews').fetchone()[0],0)

    def test_skipping_news_cancels_its_preview_and_does_not_prepare_another(self):
        plugins.set_plugin('track_previews',True);self.svc.preview_enabled=True
        self.svc.preview_job={'id':'skip','rid':'42','refs':'["one"]','after_news':'edition',
                              'snapshot':json.dumps(self.snapshot),'expires':time.time()+60}
        self.svc.db.execute("INSERT INTO music_previews(id,status) VALUES('skip','QUEUED')")
        asyncio.run(self.svc.dispatch('skip-news'))
        self.assertIsNone(self.svc.preview_job)
        self.assertEqual(self.svc.db.execute("SELECT status FROM music_previews WHERE id='skip'").fetchone()[0],'CANCELLED')
        self.svc.engine.command.reset_mock()
        asyncio.run(self.svc._preview_tick())
        self.svc.engine.command.assert_not_awaited()

    def test_news_closer_leaves_first_song_name_to_the_set_preview(self):
        voice=self.wav('voice')
        package={'id':'bulletin#1','item_id':'bulletin'}
        self.svc.pkg_jingles[package['id']]={}
        for enabled in (False,True):
            with self.subTest(enabled=enabled):
                plugins.set_plugin('track_previews',enabled)
                with patch.object(self.svc,'_say',return_value=voice), \
                        patch.object(self.svc,'_segment_voice',return_value=voice), \
                        patch.object(self.svc,'_body_segments',return_value=[('News.',False)]), \
                        patch('radio.daemon.host.back_sell',return_value=None), \
                        patch('radio.daemon.host.set_opener',return_value='Возвращаемся к музыке.') as opener, \
                        patch('radio.daemon.audio.render_package',return_value=0):
                    self.assertIsNotNone(self.svc._assemble_break([package],store.now(),True,('Artist','First')))
                self.assertEqual(opener.call_args.args[1],None if enabled else ('Artist','First'))

    def test_push_timeout_is_cancelled_by_id_before_retry(self):
        plugins.set_plugin('track_previews',True);self.svc.preview_enabled=True
        async def command(cmd):
            if cmd.startswith('previews.push'):raise asyncio.TimeoutError()
            if cmd.startswith('previews.cancel'):return 'OK'
            return json.dumps(self.snapshot)
        self.svc.engine.command.side_effect=command
        rendered={'audio_path':str(self.wav('timeout')),'speech_sec':3,'duration_sec':4,'hook_ids':[], 'announced_refs':['one','two'], 'announced_titles':['One','Two']}
        with patch('radio.previews.render',return_value=rendered):
            with self.assertRaises(asyncio.TimeoutError):asyncio.run(self.svc._preview_tick())
        self.assertTrue(self.svc.preview_job['failed'])
        asyncio.run(self.svc._preview_tick())
        self.assertIsNone(self.svc.preview_job)
        self.assertEqual(store.spoken_last_hour(self.svc.db),0)

    def test_real_engine_preview_only_continues_its_news_and_keeps_promised_queue(self):
        paths=[self.wav('song-'+str(i),6) for i in range(4)]
        playlist=self.root/'music.m3u';playlist.write_text(''.join(f'annotate:kind="music",ref="{p}":{p}\n' for p in paths))
        teaser=self.wav('teaser',6)
        edition=self.wav('edition',20)
        events=[]
        async def scenario():
            engine=Liquidsoap(playlist,.1,str(self.root/'out.wav'));engine.music_shuffle=False
            self.svc.engine=engine;self.svc.library={str(p) for p in paths}
            def event(e):events.append(e);self.svc.on_event(e)
            async def wait(predicate,seconds=30):
                deadline=time.monotonic()+seconds
                while not predicate():
                    self.assertLess(time.monotonic(),deadline,events)
                    await asyncio.sleep(.05)
            async def push(job, snapshot):
                return await engine.command('previews.push '+json.dumps({
                    'refs':[x['ref'] for x in snapshot], 'files':snapshot, 'uri':previews.uri(job)}))
            try:
                await engine.start(event)
                await wait(lambda:self.svc.current and self.svc.current['ref']==str(paths[0]))
                await engine.push_news(edition,'edition-1')
                await wait(lambda:self.svc.current and self.svc.current['ref']=='edition-1')
                self.assertEqual(await engine.command('previews.enable 1'),'OK')
                snapshot=json.loads(await engine.command('previews.prepare'))
                self.assertEqual([x['ref'] for x in snapshot],[str(paths[1]),str(paths[2])])
                job={'id':'test','after_news':'edition-1','refs':json.dumps([x['ref'] for x in snapshot]),
                     'snapshot':json.dumps(snapshot),'expires':time.time()+60,'audio_path':str(teaser)}
                self.svc.db.execute("INSERT INTO music_previews(id,refs,titles,speech_sec,duration_sec,status) VALUES(?,?,?,2,6,'QUEUED')",('test',job['refs'],'["First","Second"]'))
                self.assertEqual(await push({**job,'after_news':'another-edition'},snapshot),'STALE')
                changed=json.loads(json.dumps(snapshot));changed[0]['size']+=1
                self.assertEqual(await push({**job,'snapshot':json.dumps(changed)},changed),'STALE')
                rid=await push(job,snapshot)
                self.assertTrue(rid.isdigit(),rid)
                self.svc.preview_job={**job,'rid':rid};self.svc.id_pending=True
                await wait(lambda:self.svc.current['kind']=='teaser')
                await wait(lambda:self.svc.current['ref']==str(paths[1]))
                self.assertEqual([(e['kind'],e['ref']) for e in events[:4]],
                                 [('music',str(paths[0])),('news','edition-1'),('teaser','test'),('music',str(paths[1]))])
                self.assertEqual(self.svc.db.execute("SELECT COUNT(*) FROM events WHERE type='MUSIC_TEASER'").fetchone()[0],1)
                snapshot=json.loads(await engine.command('previews.prepare'))
                later={**job,'id':'stale','after_news':'edition-2','snapshot':json.dumps(snapshot),
                       'refs':json.dumps([x['ref'] for x in snapshot])}
                self.assertEqual(await push(later,snapshot),'STALE','No standalone preview between songs')
                await engine.push_news(edition,'edition-2')
                await wait(lambda:self.svc.current['ref']=='edition-2')
                snapshot=json.loads(await engine.command('previews.prepare'))
                later.update(snapshot=json.dumps(snapshot),refs=json.dumps([x['ref'] for x in snapshot]))
                rid=await push(later,snapshot)
                self.assertTrue(rid.isdigit(),rid)
                await engine.push_replay(paths[0],'music',str(paths[0]),0,'manual',queue='fresh')
                await wait(lambda:self.svc.current['ref']==str(paths[0]))
                self.assertFalse(any(e.get('ref')=='stale' for e in events))
            finally:
                await engine.stop()
        with patch.dict(os.environ,RADIO_FILE_SYNC='realtime'):
            asyncio.run(scenario())
