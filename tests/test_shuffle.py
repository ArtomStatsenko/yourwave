import asyncio
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from radio import config, settings
from radio.daemon import Service
from radio.engine import Liquidsoap
from radio.tts import MockTTS


class ShuffleTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-shuffle-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR', self.root), ('RUN_DIR', self.root / 'run')]:
            override = patch.object(config, name, value)
            override.start()
            self.addCleanup(override.stop)
        self.cfg = config.load()

    def test_saved_shuffle_is_independent_of_experiments_and_source(self):
        defaults = settings.base(self.cfg)
        self.assertTrue(self.cfg['music']['shuffle'])
        settings.set_setting('music_shuffle', 0)
        settings.apply(self.cfg, defaults)
        self.assertFalse(self.cfg['music']['shuffle'])
        self.assertEqual(self.cfg['music']['source'], 'local')
        self.assertEqual(next(s for s in settings.listing(config.load()) if s['key'] == 'music_shuffle')['value'], 0)
        settings.reset('music_shuffle')
        settings.apply(self.cfg, defaults)
        self.assertTrue(self.cfg['music']['shuffle'])

    def test_live_sync_retries_failure_and_sends_only_changes(self):
        svc = Service(self.cfg, tts=MockTTS())
        self.addCleanup(svc.db.close)
        svc.engine = AsyncMock()
        svc.engine.music_shuffle = True
        svc.engine.command.side_effect = ['ERROR', 'OK']
        settings.set_setting('music_shuffle', 0)

        async def scenario():
            await svc._sync_shuffle()
            self.assertTrue(svc.engine.music_shuffle)
            await svc._sync_shuffle()
            await svc._sync_shuffle()
        asyncio.run(scenario())
        self.assertFalse(svc.engine.music_shuffle)
        self.assertEqual([c.args[0] for c in svc.engine.command.await_args_list], ['music.shuffle 0'] * 2)

    def test_ui_switch_renders_and_saves_unchecked_as_zero(self):
        source = (config.PKG_DIR / 'ui.html').read_text()
        view = source[source.index('function settingValue('):source.index("$('audio-preset').onchange=")]
        handlers = source[source.index("$('stg').oninput="):source.index("$('stg').onkeydown=")]
        harness = r'''
const assert=require('node:assert/strict');
const nodes={settings:{contains:()=>false},stg:{}};
const $=id=>{assert(nodes[id],id);return nodes[id]};
const document={activeElement:null},sample={st:'idle'},esc=String;
let stg=[{key:'music_shuffle',value:1,hint:'Remaining library',step:1}],sent;
const api=(url,body)=>{sent=body;return Promise.resolve({settings:stg})};
/* FUNCTIONS */
settingsView(true);
assert(nodes.settings.innerHTML.includes('role="switch"'));
assert(nodes.settings.innerHTML.includes(' checked'));
const input={type:'checkbox',checked:false,closest(){return this},dataset:{st:'music_shuffle'}};
nodes.stg.oninput({target:input});
nodes.stg.onchange({target:input});
assert.deepEqual(sent,{cmd:'setting',key:'music_shuffle',value:0});
settingsView(true);assert(!nodes.settings.innerHTML.includes(' checked'));
'''
        result = subprocess.run(['node', '-e', harness.replace('/* FUNCTIONS */', view + '\n' + handlers)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_plan_keeps_engine_order_and_set_membership_and_accounts_for_voice(self):
        svc=Service(self.cfg,tts=MockTTS());self.addCleanup(svc.db.close)
        svc.library={'one','two','three'};svc.state='PLAYING'
        svc.current={'kind':'music','ref':'current','duration':120,'started':1000}
        rows=[{'kind':'news','ref':'edition','duration':10},
              {'kind':'music','ref':'two','duration':180,'set':True},
              {'kind':'music','ref':'one','duration':200,'set':False},
              {'kind':'music','ref':'three','duration':0,'set':False}]
        with patch('radio.daemon.time.time',return_value=1030):plan=svc._music_plan_rows(rows)
        self.assertEqual([r['ref'] for r in plan],['two','one','three'])
        self.assertEqual([r['set'] for r in plan],[True,False,False])
        from datetime import datetime
        self.assertEqual(datetime.fromisoformat(plan[0]['at']).timestamp(),1130)
        self.assertEqual(datetime.fromisoformat(plan[1]['at']).timestamp(),1310)

    def test_plan_uses_editor_time_horizon_instead_of_track_count(self):
        from radio.daemon import PLAN_HOURS
        svc=Service(self.cfg,tts=MockTTS());self.addCleanup(svc.db.close)
        svc.library=set();svc.state='PLAYING'
        svc.current={'kind':'music','ref':'current','duration':120,'started':1000}
        rows=[{'kind':'music','ref':f'track-{i}','duration':900} for i in range(100)]
        with patch('radio.daemon.time.time',return_value=1000):plan=svc._music_plan_rows(rows)
        self.assertEqual(len(plan),48)
        from datetime import datetime
        offsets=[datetime.fromisoformat(row['at']).timestamp()-1000 for row in plan]
        self.assertTrue(all(offset<PLAN_HOURS*3600 for offset in offsets))
        svc.state='PAUSED';svc.paused_at=1000
        with patch('radio.daemon.time.time',return_value=2000):plan=svc._music_plan_rows(rows)
        self.assertEqual(len(plan),48)
        self.assertEqual(datetime.fromisoformat(plan[0]['at']).timestamp(),1120)
        self.assertTrue(all(datetime.fromisoformat(row['at']).timestamp()-1000<PLAN_HOURS*3600 for row in plan))

    def test_cold_plan_does_not_probe_audio_in_status(self):
        svc=Service(self.cfg,tts=MockTTS());self.addCleanup(svc.db.close)
        svc.library=set();svc.state='PLAYING'
        svc.current={'kind':'music','ref':'current','duration':120,'started':time.time()}
        song=self.root/'track.wav';song.write_bytes(b'fixture')
        rows=[{'kind':'music','ref':str(song),'duration':0} for _ in range(40)]
        with patch('radio.audio.duration',side_effect=AssertionError('Status must not decode audio')):
            self.assertEqual(len(svc._music_plan_rows(rows,probe=False)),1)
        self.assertTrue(svc.plan_metadata_missing)
        with patch('radio.audio.duration',return_value=60) as probe:
            asyncio.run(svc._warm_music_plan(rows))
            self.assertEqual(probe.call_count,1)
        with patch('radio.audio.duration',side_effect=AssertionError('Use warmed cache')):
            self.assertEqual(len(svc._music_plan_rows(rows,probe=False)),40)
        self.assertFalse(svc.plan_metadata_missing)

    def test_engine_plan_is_not_truncated_at_24_or_64k_bytes(self):
        import json
        tone=self.root/'tone.wav'
        with wave.open(str(tone),'wb') as stream:
            stream.setparams((2,2,44100,0,'NONE','not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03'*44100)
        playlist=self.root/'large.m3u'
        refs=[f'track-{i}-'+('x'*1100) for i in range(60)]
        playlist.write_text(''.join(f'annotate:kind="music",ref="{ref}":{tone}\n' for ref in refs))
        async def scenario():
            engine=Liquidsoap(playlist,.1,str(self.root/'out.wav'));engine.music_shuffle=False
            try:
                await engine._start(lambda event:None,True)
                deadline=time.monotonic()+10
                while not await engine.next_music():
                    self.assertLess(time.monotonic(),deadline)
                    await asyncio.sleep(.1)
                plan=await engine.music_plan()
                self.assertEqual(len(plan),60)
                self.assertGreater(len(json.dumps(plan)),65536)
            finally:
                await engine.stop()
        asyncio.run(scenario())

    def test_engine_preserves_queue_and_plays_in_order_after_toggle(self):
        tone = self.root / 'tone.wav'
        with wave.open(str(tone), 'wb') as stream:
            stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03' * (44100 * 3))
        playlist = self.root / 'music.m3u'
        refs = [f'track-{i}' for i in range(8)]
        playlist.write_text(''.join(f'annotate:kind="music",ref="{ref}":{tone}\n' for ref in refs))
        events = []

        async def scenario():
            engine = Liquidsoap(playlist, .1, str(self.root / 'out.wav'), fallback_m3u=playlist)
            engine.music_shuffle = False
            try:
                try:
                    await engine._start(events.append, True)
                except RuntimeError:
                    self.fail((self.root / 'logs/liquidsoap.log').read_text()[-4000:])
                deadline = time.monotonic() + 10
                while not await engine.next_music():
                    self.assertLess(time.monotonic(), deadline)
                    await asyncio.sleep(.1)
                before = await engine.upcoming_music()
                self.assertEqual(before, refs)
                plan=await engine.music_plan()
                import json,re
                def ref_of(row):
                    return row['ref'] or json.loads(re.search(r'ref=("(?:[^"\\]|\\.)*")',row['uri'])[1])
                self.assertEqual([ref_of(r) for r in plan],refs)
                self.assertEqual(await engine.command('music.shuffle invalid'), 'ERROR')
                self.assertEqual(await engine.command('music.shuffle 1'), 'OK')
                shuffled = await engine.upcoming_music()
                self.assertEqual([ref_of(r) for r in await engine.music_plan()],shuffled)
                self.assertEqual(shuffled[0], before[0])
                self.assertEqual(set(shuffled), set(before))
                self.assertEqual(await engine.command('music.shuffle 0'), 'OK')
                self.assertEqual(await engine.upcoming_music(), before)
                manual=await engine.push_replay(tone,'music','manual-first',0,'manual')
                first=await engine.push_replay(tone,'music','set-first',0,'',queue='fresh')
                second=await engine.push_replay(tone,'music','set-second',0,'',queue='fresh')
                plan=await engine.music_plan()
                import json,re
                refs_in_plan=[r['ref'] or json.loads(re.search(r'ref=("(?:[^"\\]|\\.)*")',r['uri'])[1]) for r in plan]
                self.assertEqual(refs_in_plan[:4],['manual-first','set-first','set-second',refs[0]])
                self.assertEqual([r['set'] for r in plan[:4]],[False,True,True,False])
                await engine.remove(manual)
                await engine.remove(first,'fresh');await engine.remove(second,'fresh')
                await engine.command('resume')
                deadline = time.monotonic() + 35
                while len([e for e in events if e.get('kind') == 'music']) < 9:
                    self.assertLess(time.monotonic(), deadline, events)
                    await asyncio.sleep(.1)
                self.assertEqual([e['ref'] for e in events if e.get('kind') == 'music'][:9], refs + refs[:1])
                await engine.command('music.source local')
                self.assertEqual(await engine.command('music.shuffle 0'), 'OK')
                self.assertEqual(await engine.upcoming_music(), refs)
                self.assertEqual(await engine.command('music.reload ' + str(playlist)), 'OK')
            finally:
                await engine.stop()
        with patch.dict('os.environ', RADIO_FILE_SYNC='realtime'):
            asyncio.run(scenario())
