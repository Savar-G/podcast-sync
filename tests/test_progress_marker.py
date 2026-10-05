"""The resume answer carries the iPhone position for the progress-bar marker."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
sys.path.insert(0, str(ROOT / "tests"))

import test_service  # noqa: E402
from podsync.podcasts_db import JsonLibrary  # noqa: E402
from test_service import COLLECTION, TRACK, episode  # noqa: E402


class MarkerTest(unittest.TestCase):
    # The same service and fake library as ServiceTest, without running its tests again.
    setUp = test_service.ServiceTest.setUp
    _service = test_service.ServiceTest._service

    def test_seek_answer_has_the_marker(self):
        r = self.svc.resume("abcdefghijk", 0)
        self.assertEqual((r["action"], r["markerTime"], r["markerLabel"]), ("seek", 2430, "40:30"))

    def test_marker_stays_when_youtube_is_newer(self):
        self.svc.progress("abcdefghijk", 3000, "pause")
        r = self.svc.resume("abcdefghijk", 3000)
        self.assertEqual((r["action"], r["reason"]), ("none", "youtube_is_newer"))
        self.assertEqual(r["markerLabel"], "40:30")
        self.assertIn("lastPlayed", r)

    def test_marker_when_already_there(self):
        r = self.svc.resume("abcdefghijk", 2425)
        self.assertEqual((r["reason"], r["markerTime"]), ("already_there", 2430))

    def test_no_marker_for_unstarted_or_finished_episodes(self):
        self.db.rows[TRACK] = episode(playhead=10.0)
        self.assertNotIn("markerTime", self.svc.resume("abcdefghijk", 0))
        self.db.rows[TRACK] = episode(playhead=3700.0)
        self.assertNotIn("markerTime", self.svc.resume("abcdefghijk", 0))

    def test_marker_is_in_video_time(self):
        for s in self.cfg.shows:
            if COLLECTION in s.apple_ids:
                s.offset_seconds = 90
        r = self.svc.resume("abcdefghijk", 0)
        self.assertEqual((r["markerTime"], r["markerLabel"], r["podcastTime"]), (2520, "42:00", 2430))

    def test_marker_never_points_past_the_video(self):
        for s in self.cfg.shows:
            if COLLECTION in s.apple_ids:
                s.offset_seconds = 1400
        r = self.svc.resume("abcdefghijk", 0)
        self.assertEqual(r["markerTime"], 3725)  # video is 3730 s long


class JsonLibraryReloadTest(unittest.TestCase):
    def test_changes_to_the_file_are_picked_up(self):
        path = Path(tempfile.mkdtemp()) / "library.json"
        row = dict(track_id=1, collection_id=2, show="s", title="t", duration=3600.0, pub_date=None, playhead=100.0)
        path.write_text(json.dumps([dict(row, last_played_ago=60)]))
        lib = JsonLibrary(path)
        self.assertEqual(lib.episode(1).playhead, 100.0)
        path.write_text(json.dumps([dict(row, playhead=900.0, last_played_ago=0)]))
        later = time.time() + 5
        os.utime(path, (later, later))  # make sure the mtime moves
        ep = lib.episode(1)
        self.assertEqual(ep.playhead, 900.0)
        self.assertAlmostEqual(ep.last_played, time.time(), delta=5)


if __name__ == "__main__":
    unittest.main()
