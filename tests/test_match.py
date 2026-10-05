import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import match  # noqa: E402
from podsync.podcasts_db import Episode  # noqa: E402
from podsync.youtube import VideoMeta, _parse_date  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


class RealWorldMatching(unittest.TestCase):
    """Recent uploads from the five channels against a snapshot of the Podcasts library."""

    # YouTube channel -> Apple Podcasts show ids, as a user might pin them in config.
    CHANNELS = {
        "UC2D2CMWXMOVWx7giW1n3LIg": [1545953110],
        "UCGq-a57w-aPwyi3pW7XLiHw": [1291423644],
        "UC6t1O76G0jYXOAoYCm153dA": [1627920305],
        "UCIaH-gZIVC432YRjNVvnyCA": [1347973549],
        "UCy2FPslt0LLPsIV0iukvHpQ": [1836497887, 1141877104],
    }

    @classmethod
    def setUpClass(cls):
        cls.videos = json.loads((FIX / "youtube_recent.json").read_text())
        cls.episodes = [Episode(**e) for e in json.loads((FIX / "podcasts_episodes.json").read_text())]
        cls.expected = json.loads((FIX / "expected_matches.json").read_text())

    def _meta(self, v):
        return VideoMeta(v["videoId"], v["title"], v["channel_id"], v["channel"], v["duration"], _parse_date(v["published"]))

    def _check_all(self, candidates_for, strict):
        checked = 0
        for v in self.videos:
            if v["videoId"] not in self.expected:
                continue
            meta = self._meta(v)
            m = match.best_match(meta, candidates_for(meta), strict=strict)
            with self.subTest(title=v["title"]):
                self.assertEqual(m.episode.track_id if m else None, self.expected[v["videoId"]])
            checked += 1
        self.assertGreaterEqual(checked, 30)

    def test_known_channel_matches_the_expected_episode(self):
        self._check_all(lambda m: [e for e in self.episodes if e.collection_id in self.CHANNELS[m.channel_id]], strict=False)

    def test_zero_config_search_across_all_shows(self):
        # No channel mapping: every show's episodes within 10 days of the upload.
        def near(m):
            return [e for e in self.episodes if abs(e.pub_date - m.published) <= 10 * 86400]

        self._check_all(near, strict=True)

    def test_titles_that_differ_still_match(self):
        # YouTube: "From HOA Management to $4B..." / Podcasts: "Bringing AI to the Real Economy"
        taubman = next(v for v in self.videos if "HOA Management" in v["title"])
        self.assertEqual(self.expected[taubman["videoId"]], 1000792339679)


class Scores(unittest.TestCase):
    def test_audio_with_ads_is_not_penalised_much(self):
        self.assertGreater(match.duration_score(2242, 2487), 0.85)

    def test_very_different_lengths_score_zero(self):
        self.assertEqual(match.duration_score(1700, 5600), 0.0)

    def test_tokens_ignore_noise_words(self):
        self.assertEqual(match.tokens("Essentials: Tools to Improve Your Focus"), {"tools", "improve", "focus"})

    def test_show_name_does_not_count_as_shared_words(self):
        self.assertEqual(match.title_score("Huberman Lab Q&A", "Sleep | Huberman Lab", show="Huberman Lab"), 0.0)


if __name__ == "__main__":
    unittest.main()
