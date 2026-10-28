import subprocess
import unittest
from pathlib import Path
from tests.ui_page import core_source, page_source, theme_source


class NewsInlineLinksTests(unittest.TestCase):
    def test_mentions_link_to_their_own_sources_without_changing_the_transcript(self):
        source = page_source()
        helpers = theme_source()[theme_source().index('function sourceAnchor('):theme_source().index('function timeline(')]
        escape = next(line for line in source.splitlines() if line.startswith('function esc('))
        result = subprocess.run(['node', '-e', core_source() + escape + '\n' + helpers + r'''
const assert=require('node:assert/strict');
var st={},open={'1':true},appearance={formatTime:x=>x};function linkedText(s){return esc(s)}
const draw=row;row=x=>draw(RadioModel.entry(x,[x],false,st));  // the theme renders the model's entries
const mlx={title:'Reddit: bestgithubrepos',url:'https://www.reddit.com/r/test/comments/a/mlxserve_native_llm_inference/'};
const tools={title:'Reddit: bestgithubrepos',url:'https://www.reddit.com/r/test/comments/b/it_tools_collection_of_handy_tools/'};
const text='На Reddit обсуждают MLX-Serve.\n\nЕщё один проект — IT Tools.';
const part={text,links:[mlx,tools]},used=new Set();
const html=newsText(part,used);
assert(html.includes('>MLX-Serve</a>'));
assert(html.includes('>IT Tools</a>'));
assert.equal(html.replace(/<[^>]*>/g,''),text);
assert.equal(used.size,2);
assert.equal((html.match(/href=/g)||[]).length,2);
let rendered=row({id:1,kind:'news',title:'Edition',parts:[part]});
assert.equal((rendered.match(/href=/g)||[]).length,2,'Inline sources are not repeated below the text');
assert(!rendered.includes('<p><a '));
const github={url:'https://github.com/vercel/next.js/releases/tag/v16',title:'vercel/next.js'};
assert(newsText({text:'Вышел Next.js.',links:[github]},new Set()).includes('>Next.js</a>'));
assert(newsText({text:'Обсуждение на j11g.com.',links:[{url:'https://j11g.com/article',title:'Tech news'}]},new Set()).includes('>j11g.com</a>'));
const unsafe={text:'<img src=x onerror=alert(1)>',links:[{url:'javascript:alert(1)',title:'img'}]};
assert.equal(newsText(unsafe,new Set()),esc(unsafe.text));
assert.doesNotThrow(()=>newsText({text:'Hello',links:[{url:'https://',title:'Hello'}]},new Set()));
used.clear();newsText({text:'На Reddit появился новый проект.',links:[mlx]},used);
assert.equal(used.size,0,'A generic mention of Reddit does not identify a story');
rendered=row({id:1,kind:'news',title:'Edition',parts:[{text:'Без названия проекта.',links:[mlx]}]});
assert(rendered.includes('Без названия проекта. · <a '),'An unmatched source stays beside the transcript');
'''], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
