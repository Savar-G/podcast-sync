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
        self._lock = threading.Lock()

    def refresh(
        self,
        track_id: int,
        max_wait: float = 10.0,
        settle: float = 1.5,
        min_wait: float = 2.5,
        running_wait: float = 1.0,
    ) -> Optional[Episode]:
        """Return the episode row after giving iCloud a chance to deliver new play state.

        Measured: a hidden launch finishes its sync writes ~2 s in. So after a
        launch we wait until the library has been quiet for `settle` seconds.
        """
        before = self.db.episode(track_id)
        key = _state_key(before)
        with self._lock:
            self._last_used = self._clock()
            launched_now = False
            if not self._running():
                log.info("launching Podcasts hidden to pull iCloud play state")
                self._launch()
                self._launched_by_us = True
                launched_now = True
        start = self._clock()
        if not launched_now:
            max_wait, min_wait = running_wait, 0.0
        activity = self.db.activity()
        last_activity = None
        while True:
            self._sleep(0.25)
            elapsed = self._clock() - start
            now = self.db.episode(track_id)
            if _state_key(now) != key:
                log.info("play state changed for %s after %.1fs", track_id, elapsed)
                return now
            sig = self.db.activity()
            if sig != activity:
                activity, last_activity = sig, self._clock()
            quiet = last_activity is not None and self._clock() - last_activity >= settle
            if elapsed >= max_wait or (quiet and elapsed >= min_wait):
                return now

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


def _state_key(ep: Optional[Episode]):
    return (round(ep.playhead, 1), ep.last_played) if ep else None
