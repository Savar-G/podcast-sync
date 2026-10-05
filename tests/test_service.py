import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import config  # noqa: E402
from podsync.podcasts_db import Episode  # noqa: E402
from podsync.service import SyncService  # noqa: E402
from podsync.state import State  # noqa: E402
from podsync.youtube import VideoMeta  # noqa: E402

SENRA = "UCy2FPslt0LLPsIV0iukvHpQ"
TRACK, COLLECTION = 1000792339679, 1836497887


class FakeDB:
    def __init__(self, episodes):
        self.rows = {e.track_id: e for e in episodes}

    def episodes(self, ids):
        return [e for e in self.rows.values() if e.collection_id in ids]

    def episode(self, track_id):
        return self.rows.get(track_id)

    def episodes_near(self, t, days=10):
        return [e for e in self.rows.values() if e.pub_date and abs(e.pub_date - t) <= days * 86400]

    def readable(self):
        return True


class FakeApp:
    def __init__(self, db):
        self.db, self.refreshed = db, []

    def refresh(self, track_id):
        self.refreshed.append(track_id)
        return self.db.episode(track_id)


def episode(**kw):
    base = dict(
        track_id=TRACK,
        collection_id=COLLECTION,
        show="David Senra",
        title="Bringing AI to the Real Economy | Alexander Taubman",
        duration=3729.0,
        pub_date=time.time() - 5 * 86400,
        playhead=2430.0,
        last_played=time.time() - 60,
    )
    base.update(kw)
    return Episode(**base)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = config.Config(shows=[config.Show("David Senra", [SENRA], [COLLECTION])])
        self.cfg.handoff_dir = self.tmp / "handoff"
        self.cfg.state_dir = self.tmp / "state"
        self.db = FakeDB([episode()])
        self.app = FakeApp(self.db)
        self.meta = VideoMeta(
            "abcdefghijk",
            "From HOA Management to $4B in Revenue & $6.3B Deal for Amex GBT | Alexander Taubman, Long Lake",
            SENRA,
            "David Senra",
            3730.0,
            time.time() - 5 * 86400,
        )
        self.svc = self._service()

    def _service(self):
        return SyncService(
            self.cfg, self.db, self.app, State(self.cfg.state_dir), fetch_meta=lambda vid: self.meta, lookup=lambda ids: []
        )

    def test_match_reports_the_episode(self):
        r = self.svc.match("abcdefghijk")
        self.assertEqual((r["matched"], r["show"]), (True, "David Senra"))

    # Apple Podcasts -> YouTube
    def test_newer_iphone_position_seeks_youtube(self):
        r = self.svc.resume("abcdefghijk", 0)
        self.assertEqual(r["action"], "seek")
        self.assertAlmostEqual(r["time"], 2430)
        self.assertEqual(r["label"], "40:30")

    def test_newer_youtube_position_wins(self):
        self.svc.progress("abcdefghijk", 3000, "pause")
        r = self.svc.resume("abcdefghijk", 3000)
        self.assertEqual((r["action"], r["reason"]), ("none", "youtube_is_newer"))

    def test_iphone_played_after_youtube_wins_again(self):
        self.svc.progress("abcdefghijk", 800, "pause")
        self.db.rows[TRACK] = episode(playhead=1500.0, last_played=time.time() + 1)
        r = self.svc.resume("abcdefghijk", 800)
        self.assertEqual((r["action"], round(r["time"])), ("seek", 1500))

    def test_offset_is_applied_both_ways(self):
        for s in self.cfg.shows:
            if COLLECTION in s.apple_ids:
                s.offset_seconds = 90
        r = self.svc.resume("abcdefghijk", 0)
        self.assertAlmostEqual(r["time"], 2520)
        p = self.svc.progress("abcdefghijk", 1000, "pause")
        self.assertAlmostEqual(p["podcastTime"], 910)

    def test_unstarted_and_finished_episodes_do_not_seek(self):
        self.db.rows[TRACK] = episode(playhead=5.0)
        self.assertEqual(self.svc.resume("abcdefghijk", 0)["reason"], "podcast_not_started")
        self.db.rows[TRACK] = episode(playhead=3700.0)
        self.assertEqual(self.svc.resume("abcdefghijk", 0)["reason"], "podcast_finished")

    def test_already_there_does_not_seek(self):
        self.assertEqual(self.svc.resume("abcdefghijk", 2425)["reason"], "already_there")

    # YouTube -> Apple Podcasts
    def test_pause_writes_the_link_for_the_shortcut(self):
        r = self.svc.progress("abcdefghijk", 820.4, "pause")
        self.assertTrue(r["saved"])
        url = (self.cfg.handoff_dir / "resume.txt").read_text().strip()
        self.assertEqual(url, f"podcasts://podcasts.apple.com/podcast/id{COLLECTION}?i={TRACK}&t=820")
        info = json.loads((self.cfg.handoff_dir / "resume.json").read_text())
        self.assertEqual(info["time"], "13:40")

    def test_heartbeats_do_not_rewrite_the_file_every_time(self):
        self.svc.progress("abcdefghijk", 100, "pause")
        r = self.svc.progress("abcdefghijk", 115, "heartbeat")
        self.assertNotIn("saved", r)

    def test_short_or_unknown_videos_are_ignored(self):
        self.meta = VideoMeta("shortclip01", "Clip", SENRA, "David Senra", 90.0, time.time())
        self.assertEqual(self.svc.progress("shortclip01", 10, "pause")["reason"], "short_video")
        self.meta = VideoMeta("otherchan01", "Cooking pasta", "UCxxxxxxxxxxxxxxxxxxxxxx", "Chef", 3600.0, time.time())
        self.assertEqual(self.svc.resume("otherchan01", 0)["reason"], "no_matching_episode")
        self.assertFalse((self.cfg.handoff_dir / "resume.txt").exists())

    def test_unknown_channel_is_found_and_learned(self):
        self.cfg.shows = []  # zero config
        r = self.svc.match("abcdefghijk")
        self.assertEqual((r["matched"], r["episode"][:20]), (True, "Bringing AI to the R"))
        self.assertEqual(self.svc.state.channel_shows(SENRA), [COLLECTION])

    def test_match_is_cached(self):
        calls = []
        svc = SyncService(
            self.cfg, self.db, self.app, State(self.cfg.state_dir), fetch_meta=lambda v: calls.append(v) or self.meta, lookup=lambda i: []
        )
        svc.progress("abcdefghijk", 10, "heartbeat")
        svc.progress("abcdefghijk", 20, "heartbeat")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
