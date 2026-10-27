import hashlib
import json
import tempfile
import unittest
import urllib.error
import wave
from pathlib import Path
from unittest.mock import patch

from radio import config, hooks, previews, store
from radio.hook_alignment import chorus, map_sections, parse, tokens


class HookTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-hooks-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name, value in [('DATA_DIR',self.root),('RUN_DIR',self.root/'run')]:
            p = patch.object(config,name,value); p.start(); self.addCleanup(p.stop)
        self.cfg = config.load()
        self.db = store.connect(); self.addCleanup(self.db.close)
        self.song = self.root/'song.wav'
        self.wav(self.song,20)

    def wav(self,path,seconds):
        path.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(path),'wb') as stream:
            stream.setparams((2,2,44100,0,'NONE','not compressed'))
            stream.writeframes(b'\xe8\x03\xe8\x03' * int(44100*seconds))

    def test_join_keeps_stereo_music_after_mono_voice(self):
        voice = self.root / 'mono-voice.wav'
        with wave.open(str(voice), 'wb') as stream:
            stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x00\x00' * 24000)
        hook = self.root / 'stereo-hook.wav'
        with wave.open(str(hook), 'wb') as stream:
            stream.setparams((2, 2, 44100, 0, 'NONE', 'not compressed'))
            # Opposite channels cancel if the music is accidentally downmixed.
            stream.writeframes(b'\xe8\x03\x18\xfc' * 44100)
        joined = previews.audio.join_packages([voice, hook], self.root / 'joined.wav')
        with wave.open(str(joined)) as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels()), (44100, 2))
            stream.setpos(66150)
            self.assertEqual(stream.readframes(1), b'\xe8\x03\x18\xfc')

    def test_global_alignment_keeps_intro_repetition_outside_the_chorus(self):
        text='[Verse]\nverse opens here\n[Pre-Chorus]\nextra line\n[Chorus 2]\nwe are stars\nwe are stars'
        heard=tokens('we are stars we are stars verse opens here extra line we are stars we are stars')
        words=[{'word':w,'start':i,'end':i+.8} for i,w in enumerate(heard)]
        result=map_sections(parse(text),words,30)
        self.assertGreater(result['sections'][0]['start'],10)
        self.assertTrue(chorus('Припев 2'))
        self.assertFalse(chorus('Pre-Chorus'))
        self.assertFalse(chorus('Post-Chorus'))
        self.assertEqual(tokens('Ёжик — don’t'),['ежик','dont'])
        with self.assertRaises(ValueError):
            map_sections(parse(text),[{'word':'bad','start':-1,'end':2}],30)

    def test_approved_hook_survives_reanalysis_and_changed_audio_is_not_ready(self):
        digest=hooks.pcm_hash(self.song)
        ref=str(self.song)
        hooks.save(self.db,ref,self.song,digest,{'start':1,'end':7},'test',{},'APPROVED')
        row=hooks.ready(self.db,ref)
        clip=Path(row['clip']); before=hashlib.sha256(clip.read_bytes()).hexdigest()
        with wave.open(str(clip)) as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels()), (44100, 2))
        hooks.save(self.db,ref,self.song,digest,{'start':8,'end':14},'test',{},'AUTO')
        self.assertEqual(hashlib.sha256(clip.read_bytes()).hexdigest(),before)
        self.wav(self.song,21)
        self.assertIsNone(hooks.ready(self.db,ref))
        self.assertEqual(self.db.execute('SELECT review FROM track_hooks').fetchone()[0],'APPROVED')

    def test_review_reject_is_retained_and_stale_source_cannot_be_approved(self):
        hooks.save(self.db,'song',self.song,hooks.pcm_hash(self.song),{'start':1,'end':7},'test',{},'CANDIDATE')
        row=self.db.execute('SELECT * FROM track_hooks').fetchone()
        hooks.review(self.db,row['id'],False)
        self.assertIsNone(hooks.ready(self.db,'song'))
        self.db.execute("UPDATE track_hooks SET review='CANDIDATE'")
        self.wav(self.song,21)
        with self.assertRaises(ValueError):hooks.review(self.db,row['id'],True)

    def test_songle_units_and_retry_classification(self):
        payload={'chorusSegments':[{'isChorus':True,'repeats':[{'start':12340,'duration':12000}]}]}
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self):return json.dumps(payload).encode()
        with patch('radio.hooks.urllib.request.urlopen',return_value=Response()) as get:
            self.assertEqual(hooks.songle(self.db,'ytmusic:123456789ab'),[{'start':12.34,'end':20.34}])
            hooks.songle(self.db,'ytmusic:123456789ab');self.assertEqual(get.call_count,1)
        for code,outcome in [(404,'MISS'),(503,'TEMP_ERROR'),(429,'TEMP_ERROR')]:
            ref='ytmusic:'+str(code)
            with patch('radio.hooks.urllib.request.urlopen',side_effect=urllib.error.HTTPError('url',code,'',{},None)):
                self.assertEqual(hooks.songle(self.db,ref),[])
            self.assertEqual(self.db.execute('SELECT outcome FROM hook_lookups WHERE lookup_key=?',(ref,)).fetchone()[0],outcome)

    def test_set_preview_uses_only_later_hook_and_ends_with_first_song_intro(self):
        voice=self.root/'voice.wav';self.wav(voice,3)
        snapshot=[{'ref':'one','path':str(self.song)},{'ref':'two','path':str(self.song)}]
        digest=hooks.pcm_hash(self.song)
        for ref in ('one','two'):
            hooks.save(self.db,ref,self.song,digest,{'start':1,'end':12},'test',{},'APPROVED')
        said=[]
        def say(text):
            said.append(text)
            return voice
        result=previews.render(self.db,snapshot,['One','Two'],say)
        self.assertLessEqual(result['duration_sec'],25)
        self.assertEqual(result['speech_sec'],6)
        later=hooks.ready(self.db,'two',self.song)
        self.assertEqual(result['hook_ids'],[later['id']])
        self.assertEqual(result['announced_refs'],['one','two'])
        self.assertEqual(said,['А сейчас — One.','Позже в этом музыкальном блоке — Two.'])
        with wave.open(result['audio_path']) as stream:
            self.assertEqual((stream.getframerate(),stream.getnchannels()),(44100,2))
            self.assertAlmostEqual(stream.getnframes()/44100,17,places=1)
        self.db.execute("UPDATE track_hooks SET review='CANDIDATE'")
        said.clear()
        result=previews.render(self.db,snapshot,['One','Two'],lambda text:voice)
        self.assertEqual(result['hook_ids'],[])
        self.assertEqual(result['announced_refs'],['one'])
        self.assertEqual(result['announced_titles'],['One'])
        self.assertAlmostEqual(result['duration_sec'],3,places=1)

    def test_set_preview_omits_later_hook_that_exceeds_the_budget(self):
        voice=self.root/'long-voice.wav';self.wav(voice,9)
        hooks.save(self.db,'two',self.song,hooks.pcm_hash(self.song),{'start':1,'end':12},'test',{},'APPROVED')
        snapshot=[{'ref':ref,'path':str(self.song)} for ref in ('one','two')]
        result=previews.render(self.db,snapshot,['One','Two'],lambda text:voice)
        self.assertEqual(result['hook_ids'],[])
        self.assertEqual(result['announced_refs'],['one'])
        self.assertEqual(result['speech_sec'],9)
        self.assertAlmostEqual(result['duration_sec'],9,places=1)

    def test_import_approval_requires_trial_hash_and_same_pcm(self):
        trial=self.root/'results/0/input.wav';self.wav(trial,20)
        entry={'track':'song.wav','clip':'0/hook.mp3','approval':'User approved','input_sha256':hashlib.sha256(trial.read_bytes()).hexdigest(),'start':1,'end':7}
        manifest=self.root/'approved.json';manifest.write_text(json.dumps([entry]))
        hooks.import_approved(manifest,self.root)
        self.assertIsNotNone(hooks.ready(self.db,str(self.song)))
        self.db.execute('DELETE FROM track_hooks');self.wav(trial,19)
        hooks.import_approved(manifest,self.root)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM track_hooks').fetchone()[0],0)

    def test_confident_alignment_is_automatic_and_missing_cached_clip_is_rebuilt(self):
        lyrics='[Verse]\nverse opens here\n[Chorus]\nwe are stars\nwe are stars'
        words=[{'word':w,'start':i,'end':i+.8} for i,w in enumerate(tokens('verse opens here we are stars we are stars'))]
        probe=type('Probe',(),{'stdout':json.dumps({'format':{'tags':{'lyrics-eng':lyrics}}})})()
        original=hooks.subprocess.run
        def run(cmd,**kwargs):
            return probe if cmd[0]=='ffprobe' and '-show_entries' in cmd and 'format_tags' in cmd else original(cmd,**kwargs)
        with patch('radio.hooks.subprocess.run',side_effect=run),patch('radio.hooks.transcribe',return_value=words):
            hooks.prepare(str(self.song),self.song,self.cfg)
        row=hooks.ready(self.db,str(self.song));self.assertEqual(row['review'],'AUTO')
        Path(row['clip']).unlink()
        with patch('radio.hooks.subprocess.run',side_effect=run),patch('radio.hooks.transcribe',side_effect=AssertionError('Should reuse the cue')):
            hooks.prepare(str(self.song),self.song,self.cfg)
        self.assertIsNotNone(hooks.ready(self.db,str(self.song)))

    def test_preview_speech_counts_once_and_cancelled_preview_does_not_count(self):
        self.db.execute("INSERT INTO music_previews(id,speech_sec,status,played_at) VALUES('one',5,'QUEUED',?)",(store.iso(store.now()),))
        self.assertEqual(store.spoken_last_hour(self.db),5)
        self.db.execute("UPDATE music_previews SET status='PLAYED'")
        self.assertEqual(store.spoken_last_hour(self.db),5)
        self.db.execute("UPDATE music_previews SET status='CANCELLED'")
        self.assertEqual(store.spoken_last_hour(self.db),0)
