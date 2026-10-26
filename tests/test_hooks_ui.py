import json
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config,hooks,plugins,store
from tests import test_ui


class HookUiTests(unittest.TestCase):
    setUp=test_ui.PageServerTests.setUp
    get=test_ui.PageServerTests.get

    def test_audio_requires_auth_and_review_is_saved_without_exposing_paths(self):
        source=config.DATA_DIR/'song.wav';source.write_bytes(b'source')
        clip=config.DATA_DIR/'cache/hooks/test.wav';clip.parent.mkdir(parents=True);clip.write_bytes(b'RIFF-test')
        db=store.connect();self.addCleanup(db.close)
        db.execute('INSERT INTO track_hooks(ref,audio_hash,source_path,source_sig,algorithm,start,end,provider,review,clip) VALUES(?,?,?,?,?,1,7,?,?,?)',
                   ('song','hash',str(source),hooks.signature(source),'test','test','CANDIDATE',str(clip)))
        hook_id=db.execute('SELECT id FROM track_hooks').fetchone()[0]
        headers={'X-Radio-Token':self.server.token,'Content-Type':'application/json'}
        self.assertEqual(self.get('/hook-preview.wav?id='+str(hook_id))[0],403)
        self.assertEqual(self.get('/hook-preview.wav?id='+str(hook_id),headers),(200,b'RIFF-test'))
        self.assertEqual(self.get('/hook-preview.wav?id=bad',headers)[0],404)
        plugins.set_plugin('track_previews',True)
        with patch('radio.__main__.send',return_value=None),patch('radio.editor.connection',return_value={'state':'no_key','detail':'','ids':[]}):
            response=json.loads(self.get('/api/status',headers)[1])
        self.assertEqual(response['hooks']['counts'],{'CANDIDATE':1})
        self.assertNotIn('source_path',json.dumps(response['hooks']))
        result=json.loads(self.get('/api/cmd',headers,json.dumps({'cmd':'hook_review','id':hook_id,'approved':True}).encode())[1])
        self.assertTrue(result['ok']);self.assertEqual(db.execute('SELECT review FROM track_hooks').fetchone()[0],'APPROVED')
        self.assertEqual(result['hooks']['candidates'],[])
        self.assertEqual(result['hooks']['counts'],{'APPROVED':1})
        db.execute('UPDATE track_hooks SET clip=?',(str(config.DATA_DIR/'outside.wav'),))
        (config.DATA_DIR/'outside.wav').write_bytes(b'private')
        self.assertEqual(self.get('/hook-preview.wav?id='+str(hook_id),headers)[0],404)
