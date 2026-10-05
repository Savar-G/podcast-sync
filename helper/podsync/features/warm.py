"""POST /warm: open Apple Podcasts hidden ahead of time.

The extension calls this when a YouTube page loads. The Mac pulls iPhone positions
from iCloud only while Podcasts runs, and a cold launch takes ~3 s to sync. If we
start it now, the sync is done before you pick a video, and the resume is instant.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict

log = logging.getLogger("podsync.warm")

# However many tabs ask, start at most one warm-up per this many seconds.
MIN_INTERVAL = 60.0


def _spawn(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="podcasts-warm", daemon=True).start()


def register(service) -> None:
    lock = threading.Lock()
    last = {"at": float("-inf")}

    def run(app_warm: Callable[[], bool]) -> None:
        try:
            app_warm()
        except Exception:  # never let a warm-up break anything else
            log.exception("warm-up failed")

    def warm(_body: Dict) -> Dict:
        app_warm = getattr(service.app, "warm", None)
        if app_warm is None:
            return {"warming": False, "reason": "unsupported"}
        if getattr(service.db, "access", None) is False:
            return {"warming": False, "reason": "library_not_readable"}
        # Until a show is pinned or found, no episode can match: do not launch Podcasts for nothing.
        if not service.cfg.shows and not service.state.data.get("channels"):
            return {"warming": False, "reason": "no_shows_yet"}
        now = time.time()
        with lock:
            if now - last["at"] < MIN_INTERVAL:
                return {"warming": False, "reason": "recent"}
            last["at"] = now
        _spawn(lambda: run(app_warm))
        return {"warming": True}

    service.handlers["warm"] = warm
