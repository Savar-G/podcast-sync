import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync.podcasts_app import PodcastsApp  # noqa: E402
from podsync.podcasts_db import Episode  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class ScriptedDB:
    """Episode row and library activity as functions of (fake) time since launch."""

    def __init__(self, clock, row_change_at=None, writes_at=()):
        self.clock, self.t0 = clock, clock.t
        self.row_change_at, self.writes_at = row_change_at, writes_at

    def _since(self):
        return self.clock.t - self.t0

    def episode(self, track_id):
        changed = self.row_change_at is not None and self._since() >= self.row_change_at
        return Episode(1, 2, "s", "e", 3600.0, None, 2430.0 if changed else 2397.0, 99.0 if changed else 1.0)

    def activity(self):
        return sum(1 for w in self.writes_at if self._since() >= w)


def make(clock, db, running=False):
    state = {"running": running, "launches": 0, "quits": 0, "hidden": True}

    def launch():
        state["launches"] += 1
        state["running"] = True

    def quit():
        state["quits"] += 1
        state["running"] = False

    app = PodcastsApp(
        db,
        idle_quit_seconds=180,
        running=lambda: state["running"],
        launch=launch,
        quit=quit,
        hidden=lambda: state["hidden"],
        sleep=clock.sleep,
        clock=clock,
    )
    return app, state


class RefreshTest(unittest.TestCase):
    def test_returns_as_soon_as_the_iphone_position_arrives(self):
        clock = Clock()
        app, state = make(clock, ScriptedDB(clock, row_change_at=1.0, writes_at=(0.9,)))
        ep = app.refresh(1)
        self.assertEqual(ep.playhead, 2430.0)
        self.assertLessEqual(clock.t - 1000, 1.25)
        self.assertEqual(state["launches"], 1)

    def test_stops_when_library_goes_quiet_after_launch(self):
        clock = Clock()  # measured on this Mac: writes at ~0.9 s and ~1.9 s, then nothing
        app, _ = make(clock, ScriptedDB(clock, writes_at=(0.9, 1.9)))
        app.refresh(1)
        self.assertAlmostEqual(clock.t - 1000, 3.5, delta=0.3)

    def test_gives_up_after_max_wait(self):
        clock = Clock()
        app, _ = make(clock, ScriptedDB(clock))
        app.refresh(1)
        self.assertAlmostEqual(clock.t - 1000, 10.0, delta=0.3)

    def test_does_not_launch_or_wait_long_when_already_running(self):
        clock = Clock()
        app, state = make(clock, ScriptedDB(clock), running=True)
        app.refresh(1)
        self.assertEqual(state["launches"], 0)
        self.assertLessEqual(clock.t - 1000, 1.0)


class IdleQuitTest(unittest.TestCase):
    def test_quits_only_what_it_launched_after_idle(self):
        clock = Clock()
        app, state = make(clock, ScriptedDB(clock, writes_at=(0.5,)))
        app.refresh(1)
        clock.t += 100
        self.assertFalse(app.quit_if_idle())
        clock.t += 100
        self.assertTrue(app.quit_if_idle())
        self.assertEqual(state["quits"], 1)

    def test_never_quits_an_app_you_opened(self):
        clock = Clock()
        app, state = make(clock, ScriptedDB(clock), running=True)
        app.refresh(1)
        clock.t += 1000
        self.assertFalse(app.quit_if_idle())

    def test_leaves_it_open_if_you_brought_the_window_up(self):
        clock = Clock()
        app, state = make(clock, ScriptedDB(clock, writes_at=(0.5,)))
        app.refresh(1)
        state["hidden"] = False
        clock.t += 1000
        self.assertFalse(app.quit_if_idle())
        self.assertEqual(state["quits"], 0)


if __name__ == "__main__":
    unittest.main()
