"""Get fresh iPhone positions into the Mac library.

The Mac only pulls iCloud play state while the Podcasts app runs, so we
launch it hidden, wait for the episode row to change, and quit it again later
if (and only if) we were the ones who launched it.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Callable, Optional

from .podcasts_db import Episode, PodcastsDB

log = logging.getLogger("podsync.app")

# After a launch of ours has synced, the library is current for this long: no extra wait.
FRESH_FOR = 60.0


def is_running() -> bool:
    return subprocess.run(["pgrep", "-x", "Podcasts"], capture_output=True).returncode == 0


def launch_hidden() -> None:
    subprocess.run(["open", "-g", "-j", "-a", "Podcasts"], check=False)


def is_hidden() -> bool:
    out = subprocess.run(["lsappinfo", "info", "-app", "com.apple.podcasts"], capture_output=True, text=True).stdout
    return "(hidden)" in out.splitlines()[0] if out else False


def quit_app() -> None:
    # A hidden Podcasts takes ~35 s to exit after a quit request (measured), so ask and move on.
    script = 'ignoring application responses\ntell application "Podcasts" to quit\nend ignoring'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False, timeout=20)


class PodcastsApp:
    def __init__(
        self,
        db: PodcastsDB,
        idle_quit_seconds: float = 180,
        *,
        running: Callable[[], bool] = is_running,
        launch: Callable[[], None] = launch_hidden,
        quit: Callable[[], None] = quit_app,
        hidden: Callable[[], bool] = is_hidden,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ):
        self.db = db
        self.idle_quit_seconds = idle_quit_seconds
        self._running, self._launch, self._quit, self._hidden = running, launch, quit, hidden
        self._sleep, self._clock = sleep, clock
        self._launched_by_us = False
        self._last_used = 0.0
        self._warming = False  # a warm() launch is waiting for its iCloud sync
        self._synced_at = float("-inf")  # when a launch of ours last finished its sync
        self._lock = threading.Lock()

    def refresh(
        self,
        track_id: int,
        max_wait: float = 10.0,
        settle: float = 1.5,
        min_wait: float = 2.5,
        running_wait: float = 1.0,
        fresh_for: float = FRESH_FOR,
    ) -> Optional[Episode]:
        """Return the episode row after giving iCloud a chance to deliver new play state.

        Measured: a hidden launch finishes its sync writes ~2 s in. So after a
        launch we wait until the library has been quiet for `settle` seconds.
        If warm() launched the app moments ago, we wait for that sync instead,
        and we do not wait at all if one of our launches synced in the last `fresh_for` s.
        """
        before = self.db.episode(track_id)
        key = _state_key(before)

        def changed() -> bool:
            return _state_key(self.db.episode(track_id)) != key

        with self._lock:
            self._last_used = self._clock()
            warming = self._warming
            launched_now = False
            if not warming and not self._running():
                log.info("launching Podcasts hidden to pull iCloud play state")
                self._launch()
                self._launched_by_us = True
                launched_now = True
        if warming:
            self._wait(lambda: changed() or not self._warming, max_wait)
        elif launched_now:
            self._settle(changed, max_wait, settle, min_wait)
            self._synced_at = self._clock()
        elif self._clock() - self._synced_at < fresh_for:
            return before
        else:
            self._settle(changed, running_wait, settle, 0.0)
        return self.db.episode(track_id)

    def warm(self, max_wait: float = 10.0, settle: float = 1.5, min_wait: float = 2.5) -> bool:
        """Launch Podcasts hidden, if it is not running, and wait for its iCloud sync.

        Call it before you need a position (in a background thread: it blocks for
        ~3 s). A refresh() that comes in meanwhile waits for this sync instead of
        launching again. Returns True if it launched the app.
        """
        with self._lock:
            if self._warming or self._running():
                return False
            log.info("warming: launching Podcasts hidden to pull iCloud play state")
            self._launch()
            self._launched_by_us = True  # ours, so the idle watcher quits it later
            self._last_used = self._clock()
            self._warming = True
        try:
            self._settle(lambda: False, max_wait, settle, min_wait)
            self._synced_at = self._clock()
        finally:
            self._warming = False
        return True

    def _wait(self, done: Callable[[], bool], max_wait: float) -> None:
        start = self._clock()
        while not done() and self._clock() - start < max_wait:
            self._sleep(0.25)

    def _settle(self, changed: Callable[[], bool], max_wait: float, settle: float, min_wait: float) -> bool:
        """Wait until `changed()` or until the library has been quiet for `settle` s
        (but at least `min_wait` s), at most `max_wait` s. Returns True on a change."""
        start = self._clock()
        activity = self.db.activity()
        last_activity = None
        while True:
            self._sleep(0.25)
            elapsed = self._clock() - start
            if changed():
                log.info("play state changed after %.1fs", elapsed)
                return True
            sig = self.db.activity()
            if sig != activity:
                activity, last_activity = sig, self._clock()
            quiet = last_activity is not None and self._clock() - last_activity >= settle
            if elapsed >= max_wait or (quiet and elapsed >= min_wait):
                return False

    def quit_if_idle(self) -> bool:
        with self._lock:
            idle = self._clock() - self._last_used
            if self._launched_by_us and idle >= self.idle_quit_seconds:
                self._launched_by_us = False
                # If you opened the window yourself meanwhile, it is yours now: leave it.
                if self._running() and self._hidden():
                    log.info("quitting Podcasts after %.0fs idle", idle)
                    self._quit()
                    return True
        return False

    def start_idle_watcher(self, every: float = 30.0) -> threading.Thread:
        def loop():
            while True:
                time.sleep(every)
                try:
                    self.quit_if_idle()
                except Exception:  # never let the watcher die
                    log.exception("idle watcher")

        t = threading.Thread(target=loop, name="podcasts-idle-quit", daemon=True)
        t.start()
        return t


class StaticApp:
    """No Podcasts app at all: just read the library. Used with JsonLibrary in tests."""

    def __init__(self, db):
        self.db = db

    def refresh(self, track_id: int, **_):
        return self.db.episode(track_id)

    def warm(self) -> bool:
        return False


class SimulatedPodcasts:
    """A pretend Podcasts process for the end-to-end test (PODSYNC_FAKE_LIBRARY).

    After a launch, the library "writes" at the times measured on a real Mac, so
    PodcastsApp waits as long as it would for the real app. Pass its methods to
    PodcastsApp and use activity() as the library's activity().
    """

    WRITES_AT = (0.9, 1.9)

    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self.launched_at: Optional[float] = None
        self.launches = 0

    def running(self) -> bool:
        return self.launched_at is not None

    def launch(self) -> None:
        self.launched_at = self._clock()
        self.launches += 1

    def quit(self) -> None:
        self.launched_at = None

    def hidden(self) -> bool:
        return True

    def activity(self) -> int:
        if self.launched_at is None:
            return 0
        since = self._clock() - self.launched_at
        return sum(1 for w in self.WRITES_AT if since >= w)


def _state_key(ep: Optional[Episode]):
    return (round(ep.playhead, 1), ep.last_played) if ep else None
