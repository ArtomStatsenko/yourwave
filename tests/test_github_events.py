import json
import unittest
from datetime import timedelta
from unittest.mock import Mock,patch

from radio import github_trending as github,store,content
import test_catchup


class GithubEventTests(unittest.TestCase):
    setUp=test_catchup.CatchupTests.setUp
    story=test_catchup.CatchupTests.story
    def repo(self):
        return {'owner':'example','repo':'tool','description':'A useful tool.','stars_total':100,'stars_today':1,'rank':1}

    def release(self,rid=1,hours=1):
        return {'id':rid,'published_at':store.iso(self.at-timedelta(hours=hours)),'body':'Fixed crashes and added offline support.',
                'draft':False,'prerelease':False,'name':'Version '+str(rid),'tag_name':'v'+str(rid)}

    def test_release_adapter_rejects_undated_old_draft_and_prerelease(self):
        records=[self.release(1,30),{**self.release(2),'draft':True},{**self.release(3),'prerelease':True},self.release(4)]
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=None)
        response.read.return_value=json.dumps(records).encode()
        with patch('radio.github_trending.urllib.request.urlopen',return_value=response):
            self.assertEqual(github.fetch_release(self.repo(),self.at)['id'],4)
            response.read.return_value=json.dumps(records[:3]).encode()
            self.assertIsNone(github.fetch_release(self.repo(),self.at))

    def test_popularity_alone_is_not_news_and_new_versions_have_distinct_ids(self):
        repo=self.repo()
        with patch('radio.github_trending.fetch_release',return_value=None):
            self.assertEqual(self.svc._take_github([repo],[],[]),'nothing new')
        self.svc.editor.retell_news=Mock(return_value='В новой версии исправлены сбои и добавлен автономный режим.')
        with patch('radio.github_trending.fetch_release',return_value=self.release(1)):
            self.svc._take_github([repo],[],[])
            self.svc._take_github([repo],[],[])
        self.assertEqual(self.svc.editor.retell_news.call_count,1)
        with patch('radio.github_trending.fetch_release',return_value=self.release(2)):
            self.svc._take_github([repo],[],[])
        rows=self.svc.db.execute("SELECT id,published_at,origin_json FROM items WHERE source='github_trending'").fetchall()
        self.assertEqual(len(rows),2)
        self.assertNotEqual(rows[0]['id'],rows[1]['id'])
        self.assertEqual(rows[0]['published_at'],self.release()['published_at'])
        self.assertEqual(json.loads(rows[0]['origin_json'])[0]['event_type'],'release')

    def test_new_project_requires_actual_creation_date(self):
        repo=self.repo()
        self.svc.editor.retell=Mock(return_value='Новый инструмент помогает работать с документами.')
        self.assertEqual(self.svc._take_github([],[],[repo]),'nothing new')
        self.assertEqual(self.svc._take_github([],[],[{**repo,'created_at':store.iso(self.at-timedelta(days=2))}]),'nothing new')
        with patch('radio.github_trending.fetch_readme',return_value=''):
            self.svc._take_github([],[],[{**repo,'created_at':store.iso(self.at-timedelta(hours=2))}])
        row=self.svc.db.execute("SELECT published_at FROM items WHERE id='github:example/tool'").fetchone()
        self.assertEqual(row[0],store.iso(self.at-timedelta(hours=2)))

    def test_legacy_discovery_is_retired_without_deleting_source_or_history(self):
        self.story('legacy')
        self.svc.db.execute("UPDATE items SET source='github_trending' WHERE id='legacy'")
        github.retire_undated(self.svc.db)
        self.assertEqual(self.svc.db.execute("SELECT count(*) FROM items WHERE id='legacy'").fetchone()[0],1)
        self.assertEqual(self.svc.db.execute("SELECT status FROM packages WHERE item_id='legacy'").fetchone()[0],'SKIPPED')
