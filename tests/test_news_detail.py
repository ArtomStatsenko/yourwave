import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, editor, settings


class NewsDetailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-news-detail-')
        self.addCleanup(tmp.cleanup)
        override = patch.object(config, 'DATA_DIR', Path(tmp.name))
        override.start()
        self.addCleanup(override.stop)
        self.cfg = config.load()
        self.writer = editor.Editor(self.cfg)

    def test_modes_change_prompt_budget_validation_and_cache_together(self):
        def response(target, path, body):
            count = {200: 25, 400: 50, 800: 100}[body['max_tokens']]
            return {'choices': [{'message': {'content': ' '.join(['слово'] * count)}, 'finish_reason': 'stop'}]}
        with patch.object(self.writer, '_post', side_effect=response) as post:
            outputs = []
            for level in range(3):
                self.cfg['editor']['news_detail'] = level
                outputs.append(self.writer.prepare('Source text with facts.', 'news', 'Title'))
            self.assertEqual([len(text.split()) for text in outputs], [25, 50, 100])
            self.assertEqual(post.call_count, 3)
            self.assertEqual(len(list((config.DATA_DIR / 'cache/edits').glob('*.txt'))), 3)
            self.cfg['editor']['news_detail'] = 1
            self.assertEqual(self.writer.prepare('Source text with facts.', 'news', 'Title'), outputs[1])
            self.assertEqual(post.call_count, 3)
        self.assertIsNone(editor.check(outputs[2], editor.NEWS_DETAIL[0]['words']))

    def test_detail_is_live_persisted_and_has_named_slider_stops(self):
        settings.set_setting('news_detail', 2)
        loaded = config.load()
        settings.apply(loaded, settings.base(loaded))
        self.assertEqual(loaded['editor']['news_detail'], 2)
        with patch.object(editor, 'connection', return_value={'state': 'no_key', 'detail': '', 'ids': []}), \
                patch.object(settings, '_voice_state', return_value={}):
            row = next(r for r in settings.listing(loaded) if r['key'] == 'news_detail')
        self.assertEqual((row['min'], row['max'], row['step'], row['value']), (0, 2, 1, 2))
        self.assertEqual(row['value_labels'], ['Brief', 'Standard', 'Discussion'])
        settings.reset('news_detail')
        settings.apply(loaded, settings.base(config.load()))
        self.assertEqual(loaded['editor']['news_detail'], 1)
        (config.DATA_DIR / 'config.toml').write_text('[editor]\nnews_detail = 3\n')
        with self.assertRaisesRegex(ValueError, 'news_detail'):
            config.load()

    def test_slider_has_names_description_and_updates_accessible_value(self):
        source = (Path(__file__).resolve().parents[1] / 'radio/ui.html').read_text()
        helper = source[source.index('function settingValue('):source.index("$('audio-preset').onchange=")]
        handler = source[source.index("$('stg').oninput="):source.index("$('stg').onchange=")]
        script = r"""
const assert=require('node:assert/strict');
var nodes={},document={activeElement:null},sample={st:'idle'};
function $(id){return nodes[id]||(nodes[id]={innerHTML:'',textContent:'',contains(){return false}})}
function esc(x){return String(x)}
var stg=[{key:'news_detail',title:'News detail',hint:'For new stories.',value:1,default:1,min:0,max:2,step:1,unit:'',value_labels:['Brief','Standard','Discussion']}];
""" + helper + handler + r"""
settingsView(true);
assert.ok($('settings').innerHTML.includes('type="range"'));
assert.ok($('settings').innerHTML.includes('aria-valuetext="Standard"'));
assert.ok($('settings').innerHTML.includes('aria-describedby="hint-news_detail"'));
assert.ok($('settings').innerHTML.includes('Brief</span><span>Standard</span><span>Discussion'));
var field={value:'2',dataset:{st:'news_detail'},attributes:{},closest(){return this},setAttribute(k,v){this.attributes[k]=v}};
$('stg').oninput({target:field});
assert.equal($('sv-news_detail').textContent,'Discussion');
assert.equal(field.attributes['aria-valuetext'],'Discussion');
assert.equal(settingValue({unit:'s'},120),'120 s');
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
