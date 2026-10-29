"""Characters stay with their scripts, through retries and edition regrouping."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, content, daemon, editor, plugins, settings, store
from radio.tts import MockTTS
from tests.ui_page import core_source, theme_source


class HostOpinionsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='radio-characters-')
        self.addCleanup(tmp.cleanup)
        for key, value in [('DATA_DIR', Path(tmp.name)), ('RUN_DIR', Path(tmp.name) / 'run')]:
            override = patch.object(config, key, value)
            override.start()
            self.addCleanup(override.stop)
        self.cfg = config.load()
        self.svc = daemon.Service(self.cfg, tts=MockTTS(), output='x')
        self.svc.tts2 = MockTTS()
        self.addCleanup(self.svc.db.close)

    def test_switch_profiles_cache_and_exact_notices(self):
        entry = next(p for p in plugins.listing(self.cfg) if p['name'] == 'host_opinions')
        self.assertFalse(entry['on'])
        self.assertEqual(entry['section'], 'experiments')
        defaults = settings.base(self.cfg)
        settings.set_setting('host_character', 'Ценит свободу и доступность.')
        settings.set_setting('cohost_character', 'Ценит приватность и надёжность.')
        settings.apply(self.cfg, defaults)
        writer = self.svc.editor
        with patch.object(writer, '_complete', return_value='Компания выпустила редактор. На мой взгляд, доступность здесь важнее новизны.') as call:
            writer.retell_news('Релиз', 'Компания выпустила редактор.')
            self.assertNotIn('Ценит свободу', call.call_args.args[0])
            plugins.set_plugin('host_opinions', True)
            writer.retell_news('Релиз', 'Компания выпустила редактор.')
            self.assertIn('Ценит свободу', call.call_args.args[0])
            writer.retell_news('Релиз', 'Компания выпустила редактор.', speaker='cohost')
            self.assertIn('Ценит приватность', call.call_args.args[0])
            self.assertNotIn('Ценит свободу', call.call_args.args[0])
            writer.retell_news('Релиз', 'Компания выпустила редактор.', speaker='cohost')
            self.assertEqual(call.call_count, 3)
            settings.set_setting('cohost_character', 'Ценит простоту.')
            settings.apply(self.cfg, defaults)
            writer.retell_news('Релиз', 'Компания выпустила редактор.', speaker='cohost')
            self.assertEqual(call.call_count, 4)
            for kind in ('reminder', 'weather', 'announcement'):
                self.assertEqual(writer.prepare('Точный текст.', kind), 'Точный текст.')
            self.assertEqual(call.call_count, 4)
        settings.set_setting('host_character', '')
        settings.apply(self.cfg, defaults)
        self.assertEqual(writer._character(), '')

    def test_character_voice_is_persisted_for_retries_single_and_regrouped_editions(self):
        plugins.set_plugin('host_opinions', True)
        plugins.set_plugin('duo_hosts', True)
        iid = next(str(i) for i in range(100) if editor.host_for(self.cfg, str(i)) == 'cohost')
        content.add_item(self.svc.db, self.cfg, {'id': iid, 'source': 'reddit', 'topic': 'Reddit: example',
                         'url': '', 'published_at': store.now(), 'body': 'Компания выпустила редактор.'}, 'raw_post')
        p = dict(self.svc.db.execute('SELECT * FROM packages').fetchone())
        with patch.object(self.svc.editor, '_ask', return_value='Компания выпустила редактор. На мой взгляд, полезное решение.') as ask:
            self.svc._prepare_package(p)
            self.assertIn(self.cfg['broadcast']['cohost_character'], ask.call_args.args[0])
            self.svc._prepare_package(p)
            ask.assert_called_once()
        self.assertIs(self.svc._package_voice(p), self.svc.tts2)
        for owners in ([p['id']], [p['id'], p['id']]):
            self.assertEqual(self.svc._duo([], owners, [p])[2], [self.svc.tts2] * len(owners))
        plugins.set_plugin('host_opinions', False)
        self.assertIs(self.svc._package_voice(p), self.svc.tts2)
        plugins.set_plugin('duo_hosts', False)
        self.assertIsNone(self.svc._package_voice(p))

    def test_prepared_source_character_survives_ingestion_and_database_reopen(self):
        content.add_item(self.svc.db, self.cfg, {'id': 'github:test', 'source': 'github_new', 'topic': 'Проект',
                         'url': '', 'published_at': store.now(), 'body': 'Вышел новый редактор.',
                         'text_prepared': True, 'editorial_host': 'cohost'}, 'raw_post')
        db = store.connect()
        self.addCleanup(db.close)
        self.assertEqual(db.execute('SELECT editorial_host FROM packages').fetchone()[0], 'cohost')

    def test_config_rejects_invalid_character_before_starting_the_station(self):
        for value in ('42', '"' + 'x' * 1201 + '"'):
            (config.DATA_DIR / 'config.toml').write_text('[broadcast]\nhost_character = ' + value + '\n')
            with self.assertRaisesRegex(ValueError, 'host_character'):
                config.load()

    def test_visual_only_example_is_withheld_before_speech(self):
        for title in ('кто будет покупать ЭТО в здравом уме?', 'Яма.'):
            content.add_item(self.svc.db, self.cfg, {'id': title, 'source': 'reddit', 'topic': 'Reddit: kafkafps',
                             'url': '', 'published_at': store.now(), 'body': title}, 'raw_post')
            p = dict(self.svc.db.execute('SELECT * FROM packages WHERE item_id=?', (title,)).fetchone())
            with patch.object(self.svc.editor, '_complete', return_value='SKIP') as call, \
                    patch.object(self.svc.tts, 'synthesize') as synth:
                self.svc._render(p)
                self.assertIn('без экрана, картинки', call.call_args.args[0])
                self.assertIn('источник, а не название проекта', call.call_args.args[0])
                synth.assert_not_called()
            self.assertEqual(self.svc.db.execute('SELECT status FROM packages WHERE id=?', (p['id'],)).fetchone()[0], 'SKIPPED')

    def test_character_fields_render_as_escaped_labelled_textareas(self):
        source = theme_source()
        helper = source[source.index('function helpText('):source.index('function pluginButton(')]
        helper += source[source.index('function settingValue('):source.index("$('audio-preset').onchange=")]
        script = """
const assert=require('node:assert/strict');
var nodes={},document={activeElement:null},sample={st:'idle'};
function $(id){return nodes[id]||(nodes[id]={innerHTML:'',contains(){return false}})}
function esc(x){return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;')}
var stg=[{key:'host_name',title:'Host name',hint:'About the host.',value:'Бобби',default:'Бобби',min:1,max:30,step:null,unit:''},
{key:'cohost_name',title:'Co-host name',hint:'About the co-host.',value:'Джесс',default:'Джесс',min:1,max:30,step:null,unit:''},
{key:'cohost_character',title:'Co-host character',hint:'New scripts.',paragraph:true,value:'Спокойная.',default:'Спокойная.',min:0,max:1200,step:null,unit:''},
{key:'host_character',title:'Host character',hint:'Applies to new scripts.',paragraph:true,
value:'</textarea><script>bad()</script>',default:'Default',min:0,max:1200,step:null,unit:''}];
""" + helper + r"""
settingsView(true);
var html=$('settings').innerHTML;
assert.ok(html.includes('<textarea rows="4" id="st-host_character"'));
assert.ok(html.includes('aria-describedby="hint-host_character"'));
assert.ok(html.includes('label for="st-host_character"'));
assert.ok(html.includes('&lt;/textarea>'));
assert.ok(!html.includes('<script>'));
assert.ok(!html.includes('Default:'));
assert.ok(html.includes('aria-label="Reset Host character"'));
assert.ok(html.indexOf('data-rs="host_character"')<html.indexOf('<textarea'));
var hostDetails=html.match(/<details[^>]*id="help-host_name"[^>]*>([\s\S]*?)<\/details>/)[1];
var cohostDetails=html.match(/<details[^>]*id="help-cohost_name"[^>]*>([\s\S]*?)<\/details>/)[1];
assert.ok(hostDetails.includes('id="st-host_character"'));
assert.ok(!hostDetails.includes('id="st-cohost_character"'));
assert.ok(cohostDetails.includes('id="st-cohost_character"'));
assert.equal((hostDetails.match(/<summary/g)||[]).length,1);
assert.ok(!html.includes('id="help-host_name" open'));
helpText.open.host_name=true;
stg[3].value=stg[3].default;
settingsView(true);
assert.ok(!$('settings').innerHTML.includes('data-rs="host_character"'));
assert.ok($('settings').innerHTML.includes('id="help-host_name" open'));
"""
        result = subprocess.run(['node', '-e', core_source() + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
