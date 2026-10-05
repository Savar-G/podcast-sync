"""POST /known: Apple Podcasts progress for the thumbnails on a YouTube page."""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
sys.path.insert(0, str(ROOT / "tests"))

import test_service  # noqa: E402
from podsync.service import BadRequest  # noqa: E402
from test_service import TRACK, episode  # noqa: E402

VID = "abcdefghijk"


class KnownTest(unittest.TestCase):
    setUp = test_service.ServiceTest.setUp
    _service = test_service.ServiceTest._service

    def known(self, ids):
        return self.svc.handlers["known"]({"videoIds": ids})

    def matched(self):
        self.svc.match(VID)  # the helper matched this video before (you watched it once)
        self.app.refreshed.clear()

    def test_started_episode_has_progress(self):
        self.matched()
        r = self.known([VID, "zzzzzzzzzzz"])
        self.assertEqual(list(r), [VID])
        self.assertEqual(r[VID]["label"], "40:30 of 1:02:09")
        self.assertAlmostEqual(r[VID]["fraction"], 2430 / 3729, places=3)
        self.assertIn("lastPlayed", r[VID])

    def test_no_network_no_podcasts_launch_no_new_matching(self):
        def no_network(video_id):
            raise AssertionError("must not fetch YouTube")

        self.svc.fetch_meta = no_network
        self.assertEqual(self.known([VID]), {})  # never matched: unknown, left out
        self.assertEqual(self.app.refreshed, [])
        self.assertIsNone(self.svc.state.video(VID))

    def test_unstarted_and_finished_episodes_are_left_out(self):
        self.matched()
        self.db.rows[TRACK] = episode(playhead=10.0)
        self.assertEqual(self.known([VID]), {})
        self.db.rows[TRACK] = episode(playhead=3700.0)
        self.assertEqual(self.known([VID]), {})

    def test_cached_no_match_is_left_out(self):
        self.svc.state.set_video("noepisode01", {"reason": "no_matching_episode", "at": 0})
        self.assertEqual(self.known(["noepisode01"]), {})

    def test_duplicates_read_the_library_once(self):
        self.matched()
        reads = []
        get = self.db.episode
        self.db.episode = lambda t: reads.append(t) or get(t)
        self.assertEqual(list(self.known([VID, VID, VID])), [VID])
        self.assertEqual(reads, [TRACK])

    def test_bad_requests(self):
        for body in ({}, {"videoIds": "abcdefghijk"}, {"videoIds": [1]}, {"videoIds": ["../etc/pass"]}, {"videoIds": [VID] * 61}):
            with self.assertRaises(BadRequest, msg=str(body)[:60]):
                self.svc.handlers["known"](body)
        self.assertEqual(self.known([]), {})
        self.assertEqual(len(self.known(["a" * 11] * 60)), 0)  # 60 is fine

    def test_unreadable_library_answers_empty(self):
        self.matched()
        self.db.access = False
        self.assertEqual(self.known([VID]), {})
        self.db.access = True

        def locked(track_id):
            raise sqlite3.OperationalError("database is locked")

        self.db.episode = locked
        with self.assertLogs("podsync.known", "WARNING"):
            self.assertEqual(self.known([VID]), {})


if __name__ == "__main__":
    unittest.main()
