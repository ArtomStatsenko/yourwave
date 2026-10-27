import asyncio
import json
import os
import tempfile
import unittest
import time
import wave
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from radio import catchup, config, content, store, timeline
from radio.daemon import Service, BREAK_SEP
from radio.tts import MockTTS


class CatchupTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='radio-catchup-');self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        for key,value in [('DATA_DIR',self.root),('RUN_DIR',self.root/'run'),('SOCK',self.root/'run/radio.sock')]:
            p=patch.object(config,key,value);p.start();self.addCleanup(p.stop)
        self.cfg=config.load();self.cfg['broadcast']['max_break_speech_sec']=240
        self.svc=Service(self.cfg,tts=MockTTS());self.addCleanup(self.svc.db.close)
        self.at=store.now()
        p=patch('radio.daemon.now',return_value=self.at);p.start();self.addCleanup(p.stop)
        self.svc.editor.retell_news=Mock(return_value='Разработчики выпустили новую версию инструмента.')
        self.svc.editor.retell_roundup=Mock(return_value='Сегодня несколько находок для разработчиков. Разработчики выпустили новую версию инструмента.')

    def story(self,name,hours=1):
        published=self.at-timedelta(hours=hours)
        content.add_item(self.svc.db,self.cfg,{'id':name,'source':'inbox','topic':name,'url':'https://example.com/'+name,
            'published_at':published,'body':'Разработчики выпустили новую версию инструмента.',
            'text_prepared':True,'origin':[{'repo':name,'url':'https://example.com/'+name,'description':'Описание новой версии инструмента.'}]},'raw_post')
        return name

    def test_archive_dates_and_unheard_filter_survive_reservation_and_completion(self):
        self.story('old',30);self.story('too-old',49);self.story('heard');self.story('new')
        self.svc.db.execute("UPDATE packages SET status='PLAYED' WHERE item_id='heard'")
        self.assertEqual({r['id'] for r in catchup.candidates(self.svc.db,self.at)},{'old','new'})
        self.svc._build_catchup()
        active=catchup.active(self.svc.db)
        self.assertIsNotNone(active)
        originals=catchup.origins(self.svc.db,active['id'])
        self.assertEqual({r['item_id'] for r in originals},{'old','new'})
        old=next(o for o in originals if o['item_id']=='old')
        self.assertEqual(old['published_at'],store.iso(self.at-timedelta(hours=30)))
        self.assertIn('Описание новой версии',old['description'])
        text=self.svc.db.execute('SELECT group_concat(prepared_text,char(10)) FROM packages WHERE item_id=?',(active['id'],)).fetchone()[0]
        self.assertIn('Сегодня несколько находок',text)
        self.assertNotIn('Новость от',text)
        self.svc.editor.retell_roundup.assert_called_once()
        self.svc.editor.retell_news.assert_not_called()
        self.assertEqual({s['date'] for s in self.svc.editor.retell_roundup.call_args.args[0]},
                         {content.parse_time(o['published_at']).astimezone(ZoneInfo(self.cfg['weather']['timezone'])).strftime('%Y-%m-%d') for o in originals})
        self.assertEqual(catchup.candidates(self.svc.db,self.at),[])
        self.svc.db.execute("UPDATE packages SET status='READY',speech_sec=20 WHERE item_id=?",(active['id'],))
        parts=self.svc._pick_break()
        self.assertTrue(parts)
        self.assertTrue(all(p['item_id']==active['id'] for p in parts))
        self.svc.on_event({'kind':'news','ref':BREAK_SEP.join(p['id'] for p in parts)})
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM events WHERE type='CATCHUP_ITEM'").fetchone()[0],0)
        self.svc.on_event({'kind':'music','ref':str(self.root/'song.wav')})
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM events WHERE type='CATCHUP_ITEM'").fetchone()[0],2)
        self.assertEqual(self.svc.db.execute("SELECT status FROM packages WHERE item_id='new'").fetchone()[0],'SKIPPED')
        self.assertEqual(timeline._build(self.svc.db,2,10)[1]['title'],'What I missed · 48h')

    def test_skip_releases_originals_without_claiming_they_were_heard(self):
        self.story('new');self.svc._build_catchup()
        active=catchup.active(self.svc.db)
        self.svc.db.execute("UPDATE packages SET status='READY',speech_sec=10 WHERE item_id=?",(active['id'],))
        parts=self.svc._pick_break()
        self.svc.on_event({'kind':'news','ref':BREAK_SEP.join(p['id'] for p in parts)})
        self.svc.skip_flag=True
        self.svc.on_event({'kind':'music','ref':str(self.root/'song.wav')})
        self.assertEqual(catchup.reserved(self.svc.db),set())
        self.assertEqual([r['id'] for r in catchup.candidates(self.svc.db,self.at)],['new'])
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM events WHERE type='CATCHUP_ITEM'").fetchone()[0],0)

    def test_double_click_prepares_one_roundup_and_rechecks_news_heard_during_generation(self):
        self.story('new');self.svc.state='PLAYING'
        async def scenario():
            first=await self.svc.dispatch('catchup')
            task=self.svc.catchup_task
            await self.svc.dispatch('catchup')
            self.assertIs(self.svc.catchup_task,task)
            await task
            return first
        self.assertTrue(asyncio.run(scenario())['ok'])
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM items WHERE source='catchup'").fetchone()[0],1)

    def test_generation_discards_a_story_that_started_airing_meanwhile(self):
        self.story('new')
        def prepare(*args):
            rows=catchup.candidates(self.svc.db,self.at)
            self.svc.db.execute("UPDATE packages SET status='PLAYING' WHERE item_id='new'")
            return [{**r,'piece':'Новость от 27.10.2026. Текст.'} for r in rows]
        with patch('radio.daemon.catchup.prepare',side_effect=prepare):
            with self.assertRaisesRegex(ValueError,'No unplayed'):
                self.svc._build_catchup()
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM items WHERE source='catchup'").fetchone()[0],0)

    def test_source_change_during_joint_edit_discards_the_whole_script(self):
        self.story('new')
        def retell(*args):
            self.svc.db.execute("UPDATE packages SET status='PLAYING' WHERE item_id='new'")
            return 'Эта новость уже прозвучала за время подготовки.'
        self.svc.editor.retell_roundup.side_effect=retell
        with self.assertRaisesRegex(ValueError,'sources changed'):
            self.svc._build_catchup()
        self.assertIsNone(catchup.active(self.svc.db))

    def test_joint_edit_failure_does_not_queue_raw_source_text(self):
        self.story('new')
        self.svc.editor.retell_roundup.return_value=None
        with self.assertRaisesRegex(ValueError,'coherent catch-up'):
            self.svc._build_catchup()
        self.assertIsNone(catchup.active(self.svc.db))
        self.assertEqual(catchup.reserved(self.svc.db),set())

    def test_joint_context_preserves_selection_dates_and_does_not_clip_long_sources(self):
        from radio import plugins
        plugins.set_plugin('horizon',True)
        self.story('digest');self.story('long')
        self.svc.db.execute("UPDATE items SET source='horizon' WHERE id='digest'")
        self.svc.db.execute("UPDATE packages SET prepared_text=? WHERE item_id='long'",('Описание. '*1000,))
        self.svc._build_catchup()
        stories=self.svc.editor.retell_roundup.call_args.args[0]
        self.assertEqual(len(stories),1)
        self.assertEqual(stories[0]['date_kind'],'selection')
        self.assertEqual(stories[0]['text'],self.svc.db.execute("SELECT prepared_text FROM packages WHERE item_id='digest'").fetchone()[0])
        self.assertNotIn('long',catchup.reserved(self.svc.db))

    def test_failed_part_releases_the_roundup_for_retry(self):
        from unittest.mock import AsyncMock
        self.story('new');self.svc._build_catchup()
        iid=catchup.active(self.svc.db)['id']
        self.svc.db.execute("INSERT INTO packages(id,item_id,part,parts,status) VALUES(?,?,2,2,'FAILED')",(iid+'#2',iid))
        with patch.object(self.svc,'schedule',new_callable=AsyncMock),patch.object(self.svc,'_maybe_music_link',new_callable=AsyncMock):
            asyncio.run(self.svc.content_tick())
        self.assertIsNone(catchup.active(self.svc.db))
        self.assertEqual(catchup.reserved(self.svc.db),set())
        self.assertIn('prepare it again',self.svc.catchup_error)

    def test_disabling_a_source_cancels_its_unqueued_roundup(self):
        from radio import plugins
        plugins.set_plugin('reddit',True)
        self.story('new')
        self.svc.db.execute("UPDATE items SET source='reddit' WHERE id='new'")
        self.svc._build_catchup()
        asyncio.run(self.svc.dispatch('plugin',{'name':'reddit','on':False}))
        self.assertIsNone(catchup.active(self.svc.db))
        self.assertEqual(catchup.reserved(self.svc.db),set())
        self.assertIn('source was disabled',self.svc.catchup_error)

    def test_public_archive_keeps_30_hour_old_post_without_normal_airing(self):
        from radio import public_news
        item=public_news.item('telegram','example','30','https://t.me/example/30',store.iso(self.at-timedelta(hours=30)),'Описание новой версии.')
        self.assertEqual(public_news.take(self.svc.db,self.cfg,[item]),[item['id']])
        self.assertEqual(self.svc.db.execute('SELECT state FROM items WHERE id=?',(item['id'],)).fetchone()[0],'EXPIRED')
        self.assertEqual(content.ready_to_air(self.svc.db),[])
        self.assertIn(item['id'],[r['id'] for r in catchup.candidates(self.svc.db,self.at)])

    def test_real_engine_renders_manual_roundup_outside_clock_and_records_completion(self):
        from radio import plugins
        self.story('archive',30)
        folder=self.root/'music';folder.mkdir()
        song=folder/'song.wav'
        with wave.open(str(song),'wb') as stream:
            stream.setparams((2,2,44100,0,'NONE','not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03'*44100*20)
        self.cfg['music']['path']=str(folder)
        for name in ('github_trending','github_new','horizon','reddit','telegram','music_facts','weather','new_tracks'):
            plugins.set_plugin(name,False)
        self.svc.output=str(self.root/'out.wav')
        async def scenario():
            task=asyncio.create_task(self.svc.run())
            async def wait(predicate):
                deadline=time.monotonic()+65
                while not predicate():
                    if task.done():
                        await task
                        self.fail('Station ended before catch-up completed')
                    self.assertLess(time.monotonic(),deadline)
                    await asyncio.sleep(.1)
            try:
                await wait(lambda:self.svc.current is not None)
                self.assertTrue((await self.svc.dispatch('catchup'))['ok'])
                await wait(lambda:self.svc.db.execute("SELECT 1 FROM events WHERE type='CATCHUP_ITEM' AND ref='archive'").fetchone())
                self.assertTrue(any(r['title']=='What I missed · 48h' for r in timeline._build(self.svc.db,2,20)))
            finally:
                await self.svc.stop()
                await task
        with patch.dict(os.environ,RADIO_FILE_SYNC='realtime'):
            asyncio.run(scenario())
