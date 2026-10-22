import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from radio import config, music_facts


class MusicFactsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="radio-facts-")
        self.addCleanup(tmp.cleanup)
        self.data = patch.object(config, "DATA_DIR", Path(tmp.name))
        self.data.start()
        self.addCleanup(self.data.stop)
        self.aid = "a6c6897a-7415-4f8d-b5a5-3a5e05f3be67"
        self.recording = {"score": 100, "title": "Stressed Out", "artist-credit": [
            {"artist": {"id": self.aid, "name": "twenty one pilots"}}]}
        self.artist = {"id": self.aid, "name": "twenty one pilots", "type": "Group",
                       "life-span": {"begin": "2009"}, "relations": [{"type": "wikidata", "url": {
                           "resource": "https://www.wikidata.org/wiki/Q7857806"}}]}
        self.wiki = {"entities": {"Q7857806": {"descriptions": {"ru": {"value": "американский дуэт"}}}}}

    def test_exact_identity_sources_and_week_cache(self):
        with patch.object(music_facts, "_get", side_effect=[{"recordings": [self.recording]}, self.artist, self.wiki]) as get:
            facts = music_facts.fetch("Twenty One Pilots", "Stressed Out")
            self.assertIn("американский дуэт", facts["text"])
            self.assertIn("2009", facts["text"])
            self.assertEqual(facts["urls"], ["https://musicbrainz.org/artist/" + self.aid,
                                            "https://www.wikidata.org/wiki/Q7857806"])
            self.assertEqual(music_facts.fetch("twenty one pilots", "Stressed Out"), facts)
            self.assertEqual(get.call_count, 3)
            with patch.object(music_facts.time, "time", return_value=music_facts.time.time() + 8 * 86400), \
                    patch.object(music_facts, "_lookup", return_value=None) as lookup:
                self.assertIsNone(music_facts.fetch("Twenty One Pilots", "Stressed Out"))
                lookup.assert_called_once()

    def test_uncertain_matches_are_silent(self):
        for change in ({"score": 99}, {"title": "Stressed Out live"}, {"artist-credit": []},
                       {"artist-credit": [{"artist": {"id": self.aid, "name": "Another artist"}}]}):
            with self.subTest(change=change), patch.object(music_facts, "_get", return_value={
                    "recordings": [dict(self.recording, **change)]}) as get:
                self.assertIsNone(music_facts._lookup("twenty one pilots", "Stressed Out"))
                self.assertEqual(get.call_count, 1)
        other = copy.deepcopy(self.recording)
        other["artist-credit"][0]["artist"]["id"] = "b" * 36
        with patch.object(music_facts, "_get", return_value={"recordings": [self.recording, other]}):
            self.assertIsNone(music_facts._lookup("twenty one pilots", "Stressed Out"))

    def test_outage_cache_retries_and_partial_source(self):
        with patch.object(music_facts, "_lookup", side_effect=OSError("offline")) as lookup:
            self.assertIsNone(music_facts.fetch("Band", "Song"))
            self.assertIsNone(music_facts.fetch("Band", "Song"))
            self.assertEqual(lookup.call_count, 1)
            with patch.object(music_facts.time, "time", return_value=music_facts.time.time() + 901):
                self.assertIsNone(music_facts.fetch("Band", "Song"))
                self.assertEqual(lookup.call_count, 2)
        with patch.object(music_facts, "_get", side_effect=[{"recordings": [self.recording]}, self.artist, OSError()]):
            facts = music_facts._lookup("twenty one pilots", "Stressed Out")
            self.assertIn("2009", facts["text"])
            self.assertEqual(len(facts["urls"]), 1)

    def test_musicbrainz_request_spacing(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        with patch.object(music_facts, "_last_mb", 10), \
                patch.object(music_facts.time, "monotonic", return_value=10.5), \
                patch.object(music_facts.time, "sleep") as sleep, \
                patch.object(music_facts.urllib.request, "urlopen", return_value=response):
            music_facts._get("https://musicbrainz.org/ws/2/recording/", fmt="json")
            self.assertAlmostEqual(sleep.call_args.args[0], .6)
