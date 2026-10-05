"""Live checks for the extension's setup page.

Adds to GET /status:
  helperVersion      this helper's version, so the page can tell an old helper apart
  shortcutInstalled  True/False: a shortcut named "Resume Podcast" exists (iCloud syncs
                     shortcuts, so the Mac list stands in for the iPhone); None = unknown
  podcastsSync       True when Apple Podcasts on this Mac synced play positions with
                     iCloud in the last few days; None = cannot tell (never False, see below)
  lastResume         the last time YouTube jumped to an Apple Podcasts position, or None

Every value comes from a cache. Slow work (the `shortcuts` command, reading a plist)
runs on a background thread at most once per CACHE_SECONDS, so /status never waits.

About "Sync Library": Apple Podcasts keeps no readable on/off flag for it. It does
record when it last synced play positions with iCloud (MTLibraryUppLastSyncTime), and
it skips that sync when Sync Library is off. A recent sync is good evidence that the
setting is on. An old or missing one proves nothing (Podcasts may simply not have run),
so we report None and the setup page asks the user to confirm by hand.
"""
from __future__ import annotations

import logging
import plistlib
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..config import HOME
from ..podcasts_db import APPLE_EPOCH, PodcastsDB

log = logging.getLogger("podsync.setup")

VERSION = "0.2"  # keep in step with server_version in server.py
SHORTCUT_NAME = "Resume Podcast"
CACHE_SECONDS = 60
SHORTCUTS_TIMEOUT = 10
SYNC_FRESH_SECONDS = 7 * 86400
PODCASTS_PREFS = HOME / "Library/Containers/com.apple.podcasts/Data/Library/Preferences/com.apple.podcasts.plist"
UPP_SYNC_KEY = "MTLibraryUppLastSyncTime"


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="podsync-setup-checks", daemon=True).start()


def _read_plist(path: Path) -> Dict[str, Any]:
    with open(path, "rb") as f:
        return plistlib.load(f)


class SetupChecks:
    def __init__(
        self,
        *,
        run: Callable[..., Any] = subprocess.run,
        read_prefs: Callable[[Path], Dict[str, Any]] = _read_plist,
        prefs_allowed: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
        spawn: Callable[[Callable[[], None]], None] = _spawn,
        prefs_path: Path = PODCASTS_PREFS,
    ):
        self._run, self._read_prefs, self._prefs_allowed = run, read_prefs, prefs_allowed
        self._clock, self._spawn, self._prefs_path = clock, spawn, prefs_path
        self._lock = threading.Lock()
        self._checked_at: Optional[float] = None
        self._refreshing = False
        self._shortcut: Optional[bool] = None
        self._sync_at: Optional[float] = None  # unix time of the last iCloud play-position sync
        self._last_resume: Optional[Dict[str, Any]] = None

    # ---- GET /status --------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        now = self._clock()
        with self._lock:
            stale = self._checked_at is None or now - self._checked_at >= CACHE_SECONDS
            start = stale and not self._refreshing
            if start:
                self._refreshing = True
            out = {
                "helperVersion": VERSION,
                "shortcutInstalled": self._shortcut,
                "podcastsSync": self._sync_state(now),
                "lastResume": dict(self._last_resume) if self._last_resume else None,
            }
        if start:
            self._spawn(self.refresh)
        return out

    def _sync_state(self, now: float) -> Optional[bool]:
        if self._sync_at is not None and now - self._sync_at <= SYNC_FRESH_SECONDS:
            return True
        return None

    # ---- background refresh -----------------------------------------------
    def refresh(self) -> None:
        try:
            shortcut = self._check_shortcut()
            sync_at = self._check_sync() if self._prefs_allowed() else None
            with self._lock:
                self._shortcut, self._sync_at = shortcut, sync_at
        finally:
            with self._lock:
                self._checked_at = self._clock()
                self._refreshing = False

    def _check_shortcut(self) -> Optional[bool]:
        try:
            r = self._run(["shortcuts", "list"], capture_output=True, text=True, timeout=SHORTCUTS_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as e:  # no `shortcuts` command, or it hung
            log.info("cannot list shortcuts: %s", e)
            return None
        if r.returncode != 0:
            log.info("shortcuts list failed (%s): %s", r.returncode, (r.stderr or "").strip()[:200])
            return None
        names = {line.strip().casefold() for line in (r.stdout or "").splitlines()}
        return SHORTCUT_NAME.casefold() in names

    def _check_sync(self) -> Optional[float]:
        try:
            value = self._read_prefs(self._prefs_path).get(UPP_SYNC_KEY)
        except Exception as e:  # missing file, no permission, damaged plist
            log.debug("cannot read Podcasts settings: %s", e)
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return None
        return float(value) + APPLE_EPOCH

    # ---- POST /resume ------------------------------------------------------
    def wrap_resume(self, original: Callable[[Dict], Dict]) -> Callable[[Dict], Dict]:
        def resume(body: Dict) -> Dict:
            result = original(body)
            if isinstance(result, dict) and result.get("action") == "seek":
                self.record_resume(result)
            return result

        return resume

    def record_resume(self, result: Dict[str, Any]) -> None:
        entry = {
            "at": int(self._clock()),
            "time": result.get("time"),
            "label": result.get("label"),
            "episode": result.get("episode"),
            "show": result.get("show"),
        }
        with self._lock:
            self._last_resume = entry


def register(service) -> None:
    db = getattr(service, "db", None)
    # Inspect this Mac only for the real library: never in unit tests or the fake-library
    # e2e run. Read Podcasts settings only after macOS allowed the library, so this check
    # can never be the one that triggers the privacy prompt.
    live = isinstance(db, PodcastsDB)
    if live:
        checks = SetupChecks(prefs_allowed=lambda: getattr(db, "access", None) is True)
    else:
        checks = SetupChecks(run=_unknown, prefs_allowed=lambda: False)
    service.setup_checks = checks
    service.status_extras.append(checks.status)
    service.handlers["resume"] = checks.wrap_resume(service.handlers["resume"])


def _unknown(*_args, **_kwargs):
    raise OSError("setup checks are off for this library")
