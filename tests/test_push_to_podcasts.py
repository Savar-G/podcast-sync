"""YouTube -> Apple Podcasts push ("ghost play"): the decision logic, with fakes only.

Nothing here runs the real podcasts-remote tool or touches the Podcasts app.
"""
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import config  # noqa: E402
from podsync.features import push_to_podcasts as push  # noqa: E402
from podsync.podcasts_db import Episode  # noqa: E402
from podsync.service import SyncService  # noqa: E402
from podsync.state import State  # noqa: E402
from podsync.youtube import VideoMeta  # noqa: E402

SENRA = "UCy2FPslt0LLPsIV0iukvHpQ"
TRACK, COLLECTION = 1000792339679, 1836497887
T0 = 1_800_000_000.0


def episode(**kw):
    base = dict(
        track_id=TRACK,
        collection_id=COLLECTION,
        show="David Senra",
        title="Bringing AI to the Real Economy | Alexander Taubman",
        duration=3729.0,
        pub_date=T0 - 5 * 86400,
        playhead=0.0,
        last_played=None,
    )
    base.update(kw)
    return Episode(**base)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Library:
    """The Podcasts library as the helper reads it."""

    def __init__(self, *episodes):
        self.rows = {e.track_id: e for e in episodes}

    def episode(self, track_id):
        return self.rows.get(track_id)

    def episodes(self, ids):
        return [e for e in self.rows.values() if e.collection_id in ids]

    def episodes_near(self, t, days=10):
        return list(self.rows.values())

    def readable(self):
        return True


class App:
    """PodcastsApp stand-in. `fresh` is what iCloud delivers when Podcasts runs."""

    def __init__(self, lib):
        self.lib, self.fresh, self.refreshed = lib, None, []

    def refresh(self, track_id, **kw):
        self.refreshed.append(track_id)
        if self.fresh:
            self.lib.rows[track_id] = self.fresh
        return self.lib.episode(track_id)


class Tool:
    """podcasts-remote stand-in: like Podcasts, it moves the library playhead on success."""

    def __init__(self, lib, clock, code=0, records=True):
        self.lib, self.clock, self.code, self.records, self.calls = lib, clock, code, records, []

    def __call__(self, tool, *args):
        self.calls.append(args)
        if self.code == 0 and self.records:
            _, collection, track, seconds = args
            old = self.lib.rows[int(track)]
            self.lib.rows[int(track)] = Episode(**{**old.__dict__, "playhead": float(seconds), "last_played": self.clock()})
        return self.code, "sent" if self.code == 0 else "nope"


def make(lib=None, **tool_kw):
    clock = Clock()
    lib = lib or Library(episode())
    app = App(lib)
    tool = Tool(lib, clock, **tool_kw)
    pusher = push.Pusher(lib, app, Path("/nonexistent/podcasts-remote"), run=tool, clock=clock, sleep=clock.sleep, start=False)
    return pusher, lib, app, tool, clock


class SkipReasonTest(unittest.TestCase):
    def test_pushes_a_far_away_position(self):
        self.assertIsNone(push.skip_reason(820, T0, episode(playhead=100, last_played=T0 - 3600)))

    def test_rules(self):
        self.assertEqual(push.skip_reason(820, T0, None), "not_in_library")
        self.assertEqual(push.skip_reason(820, T0, episode(playhead=2000, last_played=T0 + 5)), "podcast_is_newer")
        self.assertEqual(push.skip_reason(3700, T0, episode()), "near_end")  # would mark it played
        self.assertEqual(push.skip_reason(820, T0, episode(playhead=810)), "already_there")
        self.assertEqual(push.skip_reason(820, T0, episode(playhead=835)), "already_there")
        self.assertIsNone(push.skip_reason(820, T0, episode(playhead=836)))
        self.assertIsNone(push.skip_reason(100, T0, episode(playhead=2000, last_played=T0 - 1)))  # rewinding is fine


class MovedAtTest(unittest.TestCase):
    def test_a_new_position_is_fresh(self):
        self.assertEqual(push.moved_at({"time": 100, "at": T0 - 15}, {"time": 115, "at": T0}), T0)
        self.assertEqual(push.moved_at(None, {"time": 115, "at": T0}), T0)

    def test_a_position_that_did_not_move_keeps_its_age(self):
        prev = {"time": 820.2, "at": T0 - 60, "moved_at": T0 - 7200}
        self.assertEqual(push.moved_at(prev, {"time": 820.4, "at": T0}), T0 - 7200)
        self.assertEqual(push.moved_at({"time": 820.2, "at": T0 - 60}, {"time": 820.4, "at": T0}), T0 - 60)


class PusherTest(unittest.TestCase):
    def test_push_moves_podcasts_and_confirms_in_the_library(self):
        pusher, lib, app, tool, _ = make()
        job = pusher.submit(TRACK, COLLECTION, 820.4, T0)
        pusher.run_due()
        self.assertEqual(job.result, {"pushed": True, "reason": "sent"})
        self.assertEqual(tool.calls, [("push", COLLECTION, TRACK, "820.4")])
        self.assertEqual(app.refreshed, [TRACK])
        self.assertEqual((pusher.last["time"], pusher.last["pushed"]), ("13:40", True))

    def test_already_there_does_not_wake_podcasts(self):
        pusher, _, app, tool, _ = make(Library(episode(playhead=815)))
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        self.assertEqual(job.result["reason"], "already_there")
        self.assertEqual((app.refreshed, tool.calls), ([], []))

    def test_a_newer_iphone_listen_found_after_refresh_wins(self):
        pusher, _, app, tool, _ = make()
        app.fresh = episode(playhead=3000, last_played=T0 + 30)  # iCloud delivers it once Podcasts runs
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        self.assertEqual(job.result, {"pushed": False, "reason": "podcast_is_newer"})
        self.assertEqual(tool.calls, [])

    def test_podcasts_playing_on_this_mac_is_left_alone(self):
        pusher, lib, _, _, _ = make(code=push.EXIT_PODCASTS_PLAYING)
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        self.assertEqual(job.result, {"pushed": False, "reason": "podcasts_playing"})
        self.assertEqual(lib.episode(TRACK).playhead, 0.0)

    def test_tool_failure_and_unconfirmed_push_are_not_reported_as_sent(self):
        pusher, _, _, _, _ = make(code=5)
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        with self.assertLogs("podsync.push", "WARNING"):
            pusher.run_due()
        self.assertEqual(job.result["reason"], "tool_failed")

        pusher, _, _, tool, clock = make(records=False)  # tool says "sent", library never changes
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        with self.assertLogs("podsync.push", "WARNING"):
            pusher.run_due()
        self.assertEqual(job.result, {"pushed": False, "reason": "not_confirmed"})
        self.assertGreaterEqual(clock.t - T0, push.CONFIRM_WAIT)

    def test_same_spot_events_share_one_push(self):
        pusher, _, _, tool, _ = make()
        first = pusher.submit(TRACK, COLLECTION, 820, T0)  # pause
        second = pusher.submit(TRACK, COLLECTION, 821, T0)  # hidden, a moment later
        self.assertIs(first, second)
        pusher.run_due()
        third = pusher.submit(TRACK, COLLECTION, 821, T0)  # unload, after the push
        self.assertIs(third, first)
        self.assertEqual(third.result, {"pushed": True, "reason": "sent"})
        self.assertEqual(tool.calls, [("push", COLLECTION, TRACK, "821.0")])

    def test_a_video_that_moved_again_is_not_covered_by_the_old_push(self):
        pusher, _, _, tool, clock = make()
        pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        clock.t += push.DEBOUNCE
        later = pusher.submit(TRACK, COLLECTION, 830, T0 + 10)  # played on a little, paused again
        self.assertIsNone(later.result)
        pusher.run_due()
        self.assertEqual(later.result["reason"], "already_there")  # within 15 s: Podcasts keeps 13:40
        self.assertEqual(len(tool.calls), 1)

    def test_one_push_per_episode_per_debounce_window_and_the_newest_spot_wins(self):
        pusher, lib, _, tool, clock = make()
        pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        clock.t += 5
        older = pusher.submit(TRACK, COLLECTION, 900, clock.t)
        newest = pusher.submit(TRACK, COLLECTION, 960, clock.t)
        self.assertEqual(older.result, {"pushed": False, "reason": "superseded"})
        pusher.run_due()
        self.assertEqual(len(tool.calls), 1)  # still inside the window
        self.assertAlmostEqual(pusher._next_wait(), push.DEBOUNCE - 5, delta=0.01)

        clock.t += push.DEBOUNCE
        pusher.run_due()
        self.assertEqual(newest.result, {"pushed": True, "reason": "sent"})
        self.assertEqual([c[3] for c in tool.calls], ["820.0", "960.0"])
        self.assertEqual(lib.episode(TRACK).playhead, 960.0)

    def test_other_episodes_are_not_held_back_by_the_window(self):
        other = episode(track_id=TRACK + 1)
        pusher, _, _, tool, _ = make(Library(episode(), other))
        pusher.submit(TRACK, COLLECTION, 820, T0)
        pusher.run_due()
        job = pusher.submit(TRACK + 1, COLLECTION, 820, T0)
        pusher.run_due()
        self.assertTrue(job.result["pushed"])
        self.assertEqual(len(tool.calls), 2)

    def test_a_failing_push_does_not_stop_the_worker(self):
        pusher, _, app, _, _ = make()

        def boom(track_id, **kw):
            raise RuntimeError("Podcasts went away")

        app.refresh = boom
        job = pusher.submit(TRACK, COLLECTION, 820, T0)
        with self.assertLogs("podsync.push", "ERROR"):
            pusher.run_due()
        self.assertEqual(job.result, {"pushed": False, "reason": "error"})


class ProgressHandlerTest(unittest.TestCase):
    """POST /progress with the push attached, through the real SyncService."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = config.Config(shows=[config.Show("David Senra", [SENRA], [COLLECTION])])
        cfg.handoff_dir, cfg.state_dir = self.tmp / "handoff", self.tmp / "state"
        self.lib = Library(episode(playhead=100, last_played=time.time() - 86400))
        self.app = App(self.lib)
        meta = VideoMeta("abcdefghijk", "Alexander Taubman", SENRA, "David Senra", 3730.0, T0 - 5 * 86400)
        self.svc = SyncService(cfg, self.lib, self.app, State(cfg.state_dir), fetch_meta=lambda v: meta, lookup=lambda i: [])
        self.tool = Tool(self.lib, time.time)

    def attach(self, threaded):
        self.clock = Clock(time.time())  # the pusher's own clock, for the debounce window
        self.pusher = push.Pusher(
            self.lib, self.app, Path("/nonexistent"), run=self.tool, clock=self.clock, sleep=self.clock.sleep, start=threaded
        )
        push.attach(self.svc, self.pusher, toast_wait=5 if threaded else 0)

    def progress(self, t, event):
        return self.svc.handlers["progress"]({"videoId": "abcdefghijk", "currentTime": t, "event": event})

    def test_pause_waits_for_the_push_so_the_toast_can_say_sent(self):
        self.attach(threaded=True)
        r = self.progress(820.4, "pause")
        self.assertEqual((r["pushed"], r["push"], r["label"]), (True, "sent", "13:40"))
        self.assertTrue((self.svc.cfg.handoff_dir / "resume.txt").exists())  # the Shortcut fallback stays
        self.assertAlmostEqual(self.lib.episode(TRACK).playhead, 820.4)
        self.assertTrue(self.svc.status()["pushToPodcasts"]["last"]["pushed"])

    def test_leaving_the_page_pushes_without_waiting(self):
        self.attach(threaded=False)
        r = self.progress(820, "unload")
        self.assertEqual((r["pushed"], r["push"]), (False, "pending"))
        self.pusher.run_due()
        self.assertEqual(self.lib.episode(TRACK).playhead, 820.0)

    def test_heartbeats_and_seeks_never_push(self):
        self.attach(threaded=False)
        for event in ("heartbeat", "seeked"):
            r = self.progress(820, event)
            self.assertNotIn("pushed", r)
        self.pusher.run_due()
        self.assertEqual(self.tool.calls, [])

    def test_unmatched_videos_are_untouched(self):
        self.attach(threaded=False)
        self.svc.fetch_meta = lambda v: VideoMeta(v, "Cooking pasta", "UCxxxxxxxxxxxxxxxxxxxxxx", "Chef", 3600.0, T0)
        r = self.svc.handlers["progress"]({"videoId": "otherchan01", "currentTime": 50, "event": "pause"})
        self.assertEqual((r["matched"], "pushed" in r), (False, False))

    def test_closing_an_old_paused_tab_does_not_undo_a_newer_iphone_listen(self):
        self.attach(threaded=False)
        self.svc.match("abcdefghijk")
        # You paused this tab at 13:40 two hours ago. Since then you listened on the iPhone up to 50:00.
        self.svc.state.set_progress(TRACK, {"time": 820, "at": time.time() - 7200, "video_id": "abcdefghijk"})
        self.lib.rows[TRACK] = episode(playhead=3000, last_played=time.time() - 60)
        r = self.progress(820, "unload")  # now you close the tab
        self.pusher.run_due()
        self.assertEqual(self.tool.calls, [])
        self.assertEqual(self.lib.episode(TRACK).playhead, 3000)
        self.assertEqual(self.svc.status()["pushToPodcasts"]["last"]["reason"], "podcast_is_newer")
        self.assertTrue((self.svc.cfg.handoff_dir / "resume.txt").exists())  # unchanged: the Shortcut link
        self.assertEqual(r["podcastTime"], 820)

    def test_a_second_pause_at_the_same_spot_reports_the_earlier_push(self):
        self.attach(threaded=True)
        self.assertTrue(self.progress(820, "pause")["pushed"])
        r = self.progress(821, "hidden")
        self.assertEqual((r["pushed"], r["push"]), (True, "sent"))
        self.assertEqual(len(self.tool.calls), 1)

    def test_watching_on_after_a_push_pushes_again(self):
        self.attach(threaded=False)
        self.progress(820, "pause")
        self.pusher.run_due()
        self.clock.t += push.DEBOUNCE
        time.sleep(0.01)  # the video plays on: heartbeats move the position, then you pause again
        self.progress(1400, "heartbeat")
        self.progress(1500, "pause")
        self.pusher.run_due()
        self.assertEqual([c[3] for c in self.tool.calls], ["820.0", "1500.0"])


class RegisterTest(unittest.TestCase):
    def service(self, **cfg_kw):
        tmp = Path(tempfile.mkdtemp())
        cfg = config.Config(state_dir=tmp / "state", handoff_dir=tmp / "handoff", **cfg_kw)
        lib = Library(episode())
        return SyncService(cfg, lib, App(lib), State(cfg.state_dir), fetch_meta=lambda v: None, lookup=lambda i: [])

    def tool_in(self, state_dir: Path):
        state_dir.mkdir(parents=True, exist_ok=True)
        tool = state_dir / push.TOOL_NAME
        tool.write_text("#!/bin/sh\nexit 1\n")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)

    def test_off_without_the_tool_in_config_or_with_a_fake_library(self):
        svc = self.service()
        self.assertFalse(svc.status()["pushToPodcasts"]["enabled"])  # not installed

        svc = self.service(push_to_podcasts=False)
        self.tool_in(svc.cfg.state_dir)
        svc = SyncService(svc.cfg, svc.db, svc.app, svc.state)
        self.assertEqual(svc.status()["pushToPodcasts"]["reason"], "turned off in config")

    def test_on_when_installed(self):
        svc = self.service()
        self.tool_in(svc.cfg.state_dir)
        os.environ.pop("PODSYNC_FAKE_LIBRARY", None)
        svc = SyncService(svc.cfg, svc.db, svc.app, svc.state)
        self.assertTrue(svc.status()["pushToPodcasts"]["enabled"])

        os.environ["PODSYNC_FAKE_LIBRARY"] = "/tmp/x.json"
        try:
            svc = SyncService(svc.cfg, svc.db, svc.app, svc.state)
            self.assertEqual(svc.status()["pushToPodcasts"]["reason"], "fake library")
        finally:
            del os.environ["PODSYNC_FAKE_LIBRARY"]


if __name__ == "__main__":
    unittest.main()
