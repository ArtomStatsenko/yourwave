import json
import subprocess
import unittest

from radio.timeline import link_sources, mention_segments, source_label
from tests.ui_page import core_source, theme_source

MLX = {'title': 'Reddit: bestgithubrepos', 'url': 'https://www.reddit.com/r/test/comments/a/mlxserve_native_llm_inference/'}
TOOLS = {'title': 'Reddit: bestgithubrepos', 'url': 'https://www.reddit.com/r/test/comments/b/it_tools_collection_of_handy_tools/'}
TEXT = 'На Reddit обсуждают MLX-Serve.\n\nЕщё один проект — IT Tools.'

DRAW = r"""
const assert=require('node:assert/strict');
var st={},open={'1':true},appearance={formatTime:x=>x};function linkedText(s){return esc(s)}
const draw=x=>row(RadioModel.entry(x,[x],false,st));
let html=draw(matched);
assert(html.includes('>MLX-Serve</a>'));assert(html.includes('>IT Tools</a>'));
assert.equal((html.match(/href=/g)||[]).length,2,'Inline sources are not repeated below the text');
assert(!html.includes('<p><a '));
html=draw(unmatched);
assert(html.includes('Без названия проекта. · <a '),'An unmatched source stays beside the transcript');
"""


class NewsInlineLinksTests(unittest.TestCase):
    def test_mentions_link_to_their_own_sources_without_changing_the_transcript(self):
        used = set()
        segments = mention_segments({'text': TEXT, 'links': [MLX, TOOLS]}, used)
        self.assertEqual([s['text'] for s in segments if 'url' in s], ['MLX-Serve', 'IT Tools'])
        self.assertEqual(''.join(s['text'] for s in segments), TEXT)
        self.assertEqual(len(used), 2)
        github = {'url': 'https://github.com/vercel/next.js/releases/tag/v16', 'title': 'vercel/next.js'}
        self.assertIn({'text': 'Next.js', 'url': github['url']}, mention_segments({'text': 'Вышел Next.js.', 'links': [github]}, set()))
        site = {'url': 'https://j11g.com/article', 'title': 'Tech news'}
        self.assertIn('j11g.com', [s['text'] for s in mention_segments({'text': 'Обсуждение на j11g.com.', 'links': [site]}, set()) if 'url' in s])
        unsafe = {'text': '<img src=x onerror=alert(1)>', 'links': [{'url': 'javascript:alert(1)', 'title': 'img'}]}
        self.assertEqual(mention_segments(unsafe, set()), [{'text': unsafe['text']}])
        self.assertEqual(mention_segments({'text': 'Hello', 'links': [{'url': 'https://', 'title': 'Hello'}]}, set()), [{'text': 'Hello'}])
        used.clear()
        mention_segments({'text': 'На Reddit появился новый проект.', 'links': [MLX]}, used)
        self.assertEqual(used, set(), 'A generic mention of Reddit does not identify a story')

    def test_a_cyrillic_post_slug_names_its_story(self):
        # the page used to compare the percent-encoded path (%D0%BD...), so these never matched
        post = {'title': 'Reddit: pikabu', 'url': 'https://www.reddit.com/r/Pikabu/comments/x/население_гренландии_изучено_на_1/'}
        segments = mention_segments({'text': 'Заметка о том, что население Гренландии изучено примерно на один процент.', 'links': [post]}, set())
        self.assertEqual([s['text'] for s in segments if 'url' in s], ['население Гренландии изучено'])

    def test_links_are_named_by_title_post_or_site(self):
        self.assertEqual(source_label({'url': 'https://example.com/a?ref=1', 'title': 'A clear article title'}), 'A clear article title')
        self.assertEqual(source_label({'url': 'https://example.com/a?ref=1', 'title': 'example.com/a?ref=1'}), 'Source · example.com')
        self.assertEqual(source_label({'url': 'https://example.com/a?ref=1'}), 'Source · example.com')
        self.assertEqual(source_label({'url': 'https://www.reddit.com/r/BestGitHubRepos/comments/abc/mlxserve_native_llm/',
                                       'title': 'Reddit: bestgithubrepos · 2026-10-27'}), 'Reddit · mlxserve native llm')

    def test_the_theme_draws_inline_links_once_and_keeps_unmatched_sources_beside_the_text(self):
        def entry(text, links):
            e = {'id': 1, 'kind': 'news', 'title': 'Edition', 'parts': [{'text': text, 'links': [dict(l) for l in links]}]}
            link_sources(e)
            return e
        theme = theme_source()
        render = theme[theme.index('function sourceAnchor('):theme.index('function timeline(')]
        escape = next(line for line in theme.splitlines() if line.startswith('function esc('))
        data = 'const matched=' + json.dumps(entry(TEXT, [MLX, TOOLS])) + ',unmatched=' + json.dumps(entry('Без названия проекта.', [MLX])) + ';'
        result = subprocess.run(['node', '-e', core_source() + escape + '\n' + render + '\n' + data + DRAW],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
