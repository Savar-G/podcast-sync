"""Setup page checks: fast, cached, never blocking, and no real `shortcuts` calls."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import config  # noqa: E402
from podsync.features import setup_checks  # noqa: E402
from podsync.features.setup_checks import SetupChecks  # noqa: E402
from podsync.podcasts_db import APPLE_EPOCH  # noqa: E402
from podsync.service import SyncService  # noqa: E402
from podsync.state import State  # noqa: E402


class Clock:
    def __init__(self, t=2_000_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeRun:
    """Stands in for subprocess.run(["shortcuts", "list"], ...)."""

    def __init__(self, stdout="", returncode=0, error=None):
        self.stdout, self.returncode, self.error = stdout, returncode, error
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append((args, kw))
        if self.error:
            raise self.error
        return SimpleNamespace(stdout=self.stdout, stderr="", returncode=self.returncode)


class Spawn:
    """Collects background jobs so a test decides when they run."""

    def __init__(self):
        self.jobs = []

    def __call__(self, fn):
        self.jobs.append(fn)

    def run_all(self):
        jobs, self.jobs = self.jobs, []
        for fn in jobs:
            fn()


def checks(run=None, prefs=None, allowed=True, clock=None, spawn=None):
    prefs_calls = []

    def read_prefs(path):
        prefs_calls.append(path)
        if isinstance(prefs, Exception):
            raise prefs
        return prefs if prefs is not None else {}

    c = SetupChecks(
        run=run or FakeRun("Resume Podcast\n"),
        read_prefs=read_prefs,
        prefs_allowed=lambda: allowed,
        clock=clock or Clock(),
        spawn=spawn or Spawn(),
        prefs_path=Path("/nonexistent/com.apple.podcasts.plist"),
    )
    c.prefs_calls = prefs_calls
    return c


class ShortcutTest(unittest.TestCase):
    def test_first_status_is_unknown_and_does_not_block(self):
        spawn, run = Spawn(), FakeRun("Resume Podcast\n")
        c = checks(run=run, spawn=spawn)
        s = c.status()
        self.assertIsNone(s["shortcutInstalled"])
        self.assertEqual(s["helperVersion"], setup_checks.VERSION)
        self.assertEqual(run.calls, [])  # the command runs later, off the request thread
        self.assertEqual(len(spawn.jobs), 1)
        spawn.run_all()
        self.assertTrue(c.status()["shortcutInstalled"])
        args, kw = run.calls[0]
        self.assertEqual(args, ["shortcuts", "list"])
        self.assertEqual(kw["timeout"], setup_checks.SHORTCUTS_TIMEOUT)

    def test_found_among_other_shortcuts(self):
        c = checks(run=FakeRun("Morning\n  Resume Podcast  \nWork Focus\n"))
        c.refresh()
        self.assertTrue(c.status()["shortcutInstalled"])

    def test_missing(self):
        c = checks(run=FakeRun("Morning\nResume Podcasts later\n"))
        c.refresh()
        self.assertIs(c.status()["shortcutInstalled"], False)

    def test_failures_are_unknown_not_missing(self):
        for run in (
            FakeRun(error=subprocess.TimeoutExpired(["shortcuts", "list"], 10)),
            FakeRun(error=FileNotFoundError("shortcuts")),
            FakeRun("", returncode=1),
        ):
            c = checks(run=run)
            c.refresh()
            self.assertIsNone(c.status()["shortcutInstalled"], run)

    def test_result_is_cached_for_a_minute(self):
        clock, spawn, run = Clock(), Spawn(), FakeRun("Resume Podcast\n")
        c = checks(run=run, clock=clock, spawn=spawn)
        c.status()
        c.status()  # a refresh is already pending: do not start a second one
        self.assertEqual(len(spawn.jobs), 1)
        spawn.run_all()
        clock.t += 30
        c.status()
        self.assertEqual(spawn.jobs, [])
        clock.t += setup_checks.CACHE_SECONDS
        run.stdout = ""
        c.status()
        spawn.run_all()
        self.assertEqual(len(run.calls), 2)
        self.assertIs(c.status()["shortcutInstalled"], False)

    def test_a_failed_refresh_still_allows_the_next_one(self):
        clock, spawn = Clock(), Spawn()
        c = checks(run=FakeRun(error=OSError("boom")), clock=clock, spawn=spawn)
        c.status()
        spawn.run_all()
        clock.t += setup_checks.CACHE_SECONDS
        c.status()
        self.assertEqual(len(spawn.jobs), 1)


class SyncLibraryTest(unittest.TestCase):
    def apple(self, unix):
        return unix - APPLE_EPOCH

    def test_recent_icloud_sync_means_on(self):
        clock = Clock()
        c = checks(prefs={setup_checks.UPP_SYNC_KEY: self.apple(clock.t - 3600)}, clock=clock)
        c.refresh()
        self.assertIs(c.status()["podcastsSync"], True)

    def test_old_or_missing_sync_is_unknown(self):
        clock = Clock()
        for prefs in (
            {setup_checks.UPP_SYNC_KEY: self.apple(clock.t - setup_checks.SYNC_FRESH_SECONDS - 60)},
            {},
            {setup_checks.UPP_SYNC_KEY: "soon"},
            {setup_checks.UPP_SYNC_KEY: True},
            PermissionError("no access"),
        ):
            c = checks(prefs=prefs, clock=clock)
            c.refresh()
            self.assertIsNone(c.status()["podcastsSync"], prefs)

    def test_goes_unknown_when_the_sync_gets_old(self):
        clock = Clock()
        c = checks(prefs={setup_checks.UPP_SYNC_KEY: self.apple(clock.t)}, clock=clock)
        c.refresh()
        clock.t += setup_checks.SYNC_FRESH_SECONDS + 1
        self.assertIsNone(c.status()["podcastsSync"])

    def test_settings_are_not_read_before_macos_allows_access(self):
        clock = Clock()
        c = checks(prefs={setup_checks.UPP_SYNC_KEY: self.apple(clock.t)}, allowed=False, clock=clock)
        c.refresh()
        self.assertEqual(c.prefs_calls, [])
        self.assertIsNone(c.status()["podcastsSync"])


class FakeDB:
    access = True

    def episodes(self, ids):
        return []

    def episodes_near(self, t, days=10):
        return []

    def episode(self, track_id):
        return None


class ResumeTest(unittest.TestCase):
    def test_seek_is_recorded(self):
        clock = Clock()
        c = checks(clock=clock)
        seek = {"action": "seek", "time": 2430.0, "label": "40:30", "episode": "Ep", "show": "Show", "podcastTime": 2430.0}
        resume = c.wrap_resume(lambda body: seek)
        self.assertIs(resume({"videoId": "abcdefghijk"}), seek)
        self.assertEqual(
            c.status()["lastResume"], {"at": int(clock.t), "time": 2430.0, "label": "40:30", "episode": "Ep", "show": "Show"}
        )

    def test_other_answers_are_not_recorded(self):
        c = checks()
        resume = c.wrap_resume(lambda body: {"action": "none", "reason": "youtube_is_newer"})
        resume({})
        self.assertIsNone(c.status()["lastResume"])

    def test_errors_pass_through(self):
        c = checks()

        def broken(body):
            raise ValueError("bad")

        with self.assertRaises(ValueError):
            c.wrap_resume(broken)({})
        self.assertIsNone(c.status()["lastResume"])


class RegisterTest(unittest.TestCase):
    def test_plugs_into_the_service_and_stays_off_for_a_fake_library(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = config.Config(handoff_dir=tmp / "handoff", state_dir=tmp / "state")
        svc = SyncService(cfg, FakeDB(), None, State(cfg.state_dir), fetch_meta=lambda v: None, lookup=lambda i: [])
        spawn = Spawn()
        svc.setup_checks._spawn = spawn
        s = svc.status()
        for key in ("helperVersion", "shortcutInstalled", "podcastsSync", "lastResume"):
            self.assertIn(key, s)
        self.assertTrue(s["ok"])
        spawn.run_all()  # a fake library never runs `shortcuts` or reads real settings
        s = svc.status()
        self.assertIsNone(s["shortcutInstalled"])
        self.assertIsNone(s["podcastsSync"])
        r = svc.handlers["resume"]({"videoId": "abcdefghijk", "currentTime": 0})
        self.assertEqual(r["action"], "none")


if __name__ == "__main__":
    unittest.main()
