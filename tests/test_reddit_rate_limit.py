import tempfile
import unittest
import urllib.error
from email.message import Message
from email.utils import formatdate
from pathlib import Path
from unittest.mock import patch

from radio import config, public_news, reddit


class RedditRateLimitTests(unittest.TestCase):
    def test_ui_and_import_respect_the_same_server_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(config, 'RUN_DIR', Path(tmp)), patch('time.time', return_value=1000):
            headers = Message()
            headers['Retry-After'] = '120'
            error = urllib.error.HTTPError('https://www.reddit.com', 429, 'Too many requests', headers, None)
            with patch.object(public_news, 'fetch', side_effect=error) as fetch:
                with self.assertRaises(ValueError):
                    reddit.validate('bestgithubrepos', 'names')
                with self.assertRaises(ValueError):
                    reddit.validate('bestgithubrepos', 'names')
                with self.assertRaises((ValueError, urllib.error.HTTPError)):
                    reddit.fetch('python')
                self.assertEqual(fetch.call_count, 1, 'UI and importer must not retry during Reddit cooldown')
            with patch('time.time', return_value=1121), patch.object(public_news, 'fetch', return_value='<feed xmlns="http://www.w3.org/2005/Atom"/>') as fetch:
                self.assertEqual(reddit.validate('bestgithubrepos', 'names'), ['bestgithubrepos'])
                fetch.assert_called_once()

    def test_http_date_and_missing_retry_after(self):
        for after, expected in ((formatdate(1300, usegmt=True), 1300), ('', 2800), ('invalid', 2800)):
            with self.subTest(after=after), tempfile.TemporaryDirectory() as tmp, patch.object(config, 'RUN_DIR', Path(tmp)), patch('time.time', return_value=1000):
                headers = Message()
                headers['Retry-After'] = after
                error = urllib.error.HTTPError('https://www.reddit.com', 429, 'Too many requests', headers, None)
                with patch.object(public_news, 'fetch', side_effect=error), self.assertRaisesRegex(ValueError, 'Next local retry'):
                    reddit.validate('python', 'names')
                self.assertEqual(float((Path(tmp)/'reddit-rss.lock').read_text()), expected)
