"""YouTube -> iPhone without the Shortcut tap ("ghost play").

Apple Podcasts uploads a changed play position to iCloud at once, and the iPhone
picks it up from there. So when you pause or leave a matched video, the helper asks
the Podcasts app on this Mac to move that episode to the same second. The work is
done by scripts/podcasts_remote.c (MediaRemote, aimed at com.apple.podcasts only):
load the episode paused, then seek it. Nothing plays and no window opens.

Newest wins here too: a YouTube position that has not moved since before your last
Apple Podcasts listen (say, an old paused tab that you close) is never pushed.
The Shortcut link (resume.txt) is still written on every pause, as the fallback.

Measured on macOS 26.6: the library shows the new position ~0.5 s after the push,
and Podcasts sends it to iCloud ~0.3 s later.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from .. import handoff
from ..podcasts_db import Episode

log = logging.getLogger("podsync.push")

TOOL_NAME = "podcasts-remote"  # scripts/install.sh builds it into the state dir
PUSH_EVENTS = {"pause", "hidden", "unload", "navigate", "ended"}
MIN_DELTA = 15  # seconds; a smaller difference is not worth moving Podcasts
DEBOUNCE = 20  # at most one push per episode per 20 s; a newer position waits, then goes
END_MARGIN = 60  # never push into the last minute: Podcasts would mark the episode played
TOAST_WAIT = 3.0  # a "pause" reply waits this long for the result, so the toast can say "Sent"
CONFIRM_WAIT = 3.0  # how long the library may take to show the new position
CONFIRM_TOLERANCE = 2.0
SAME_SPOT = 2.0  # YouTube times closer than this count as "the video did not move"
TOOL_TIMEOUT = 30
EXIT_PODCASTS_PLAYING = 3  # podcasts_remote.c: you are listening on this Mac, nothing was done


@dataclass
class Job:
    track_id: int
    collection_id: int
    seconds: float  # Podcasts time
    moved_at: float  # when the YouTube position last changed (unix time)
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict] = None

    def finish(self, pushed: bool, reason: str) -> None:
        self.result = {"pushed": pushed, "reason": reason}
        self.done.set()


def skip_reason(target: float, moved_at: float, ep: Optional[Episode]) -> Optional[str]:
    """Why moving Podcasts to `target` seconds is not needed or not safe; None means push."""
    if ep is None:
        return "not_in_library"
    if ep.last_played and ep.last_played > moved_at:
        return "podcast_is_newer"
    if ep.duration and target > ep.duration - END_MARGIN:
        return "near_end"
    if abs(ep.playhead - target) <= MIN_DELTA:
        return "already_there"
    return None


def moved_at(prev: Optional[Dict], cur: Dict) -> float:
    """When the YouTube position last changed, from two progress reports in a row.

    Hidden, unload and navigate also fire for a video that has sat paused for hours,
    so the time of the report alone does not say how fresh the position is.
    """
    if prev and abs(float(prev.get("time", -1e9)) - float(cur["time"])) < SAME_SPOT:
        return float(prev.get("moved_at", prev.get("at", 0)))
    return float(cur["at"])


def run_tool(tool: Path, *args) -> Tuple[int, str]:
    try:
        r = subprocess.run([str(tool), *map(str, args)], capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, repr(e)
    return r.returncode, (r.stdout or r.stderr).strip()


class Pusher:
    """One worker thread, one push at a time, at most one push per episode per DEBOUNCE.

    A request that waits on its debounce window is replaced by a newer one for the
    same episode, so the last position you paused at is the one that reaches the iPhone.
    """

    def __init__(
        self,
        db,
        app,
        tool: Path,
        *,
        run: Callable[..., Tuple[int, str]] = run_tool,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        start: bool = True,
    ):
        self.db, self.app, self.tool = db, app, tool
        self._run, self._clock, self._sleep = run, clock, sleep
        self._cond = threading.Condition()
        self._pending: Dict[int, Job] = {}  # track_id -> newest request
        self._active: Optional[Job] = None
        self._done: Dict[int, Job] = {}  # track_id -> last finished request
        self._last_push: Dict[int, float] = {}  # track_id -> when the tool last ran for it
        self.last: Optional[Dict] = None  # newest outcome, for GET /status
        if start:
            threading.Thread(target=self._loop, name="podcasts-push", daemon=True).start()

    # ---- queue ------------------------------------------------------------
    def submit(self, track_id: int, collection_id: int, seconds: float, moved: float) -> Job:
        """Queue a push. Pause, then hidden, then unload at one spot give one push."""
        job = Job(int(track_id), int(collection_id), max(0.0, float(seconds)), float(moved))
        with self._cond:
            waiting = self._pending.get(job.track_id)
            if waiting and abs(waiting.seconds - job.seconds) < MIN_DELTA:
                waiting.seconds, waiting.moved_at = job.seconds, max(waiting.moved_at, job.moved_at)
                return waiting
            # The same spot, and the video has not moved since: that push already covers it.
            for prior in (self._active, self._done.get(job.track_id)):
                if (
                    prior
                    and prior.track_id == job.track_id
                    and abs(prior.seconds - job.seconds) < MIN_DELTA
                    and job.moved_at <= prior.moved_at
                    and (prior is self._active or (prior.result or {}).get("pushed"))
                ):
                    return prior
            old = self._pending.pop(job.track_id, None)
            if old:
                old.finish(False, "superseded")
            self._pending[job.track_id] = job
            self._cond.notify()
        return job

    def _wait_for(self, track_id: int, now: float) -> float:
        last = self._last_push.get(track_id)
        return 0.0 if last is None else max(0.0, last + DEBOUNCE - now)

    def _next_wait(self) -> Optional[float]:
        """Seconds until a pending request is due (0: now), or None when nothing is pending."""
        now = self._clock()
        waits = [self._wait_for(t, now) for t in self._pending]
        return min(waits) if waits else None

    def run_due(self) -> None:
        """Push every request whose debounce window has passed."""
        while True:
            with self._cond:
                now = self._clock()
                job = next((j for t, j in self._pending.items() if self._wait_for(t, now) == 0), None)
                if job is None:
                    return
                del self._pending[job.track_id]
                self._active = job
            try:
                self._push(job)
            except Exception:  # never let one bad push stop the worker
                log.exception("push to Apple Podcasts failed")
                job.finish(False, "error")
            finally:
                with self._cond:
                    self._active = None
                    self._done[job.track_id] = job
            self.last = {
                "trackId": job.track_id,
                "time": handoff.fmt_time(job.seconds),
                "at": int(self._clock()),
                **(job.result or {}),
            }

    def _loop(self) -> None:
        while True:
            with self._cond:
                wait = self._next_wait()
                while wait != 0:
                    self._cond.wait(timeout=wait)  # None: until the next submit
                    wait = self._next_wait()
            self.run_due()

    # ---- one push ------------------------------------------------------------
    def _push(self, job: Job) -> None:
        # Cheap check first: do not wake Podcasts when the library already rules it out.
        reason = skip_reason(job.seconds, job.moved_at, self.db.episode(job.track_id))
        if reason:
            return job.finish(False, reason)
        # Make sure Podcasts runs (hidden, if we start it) with fresh iCloud state, then check again:
        # your iPhone may have played this episode since the library last synced.
        reason = skip_reason(job.seconds, job.moved_at, self.app.refresh(job.track_id, running_wait=0.25))
        if reason:
            return job.finish(False, reason)

        with self._cond:
            self._last_push[job.track_id] = self._clock()
        code, out = self._run(self.tool, "push", job.collection_id, job.track_id, f"{job.seconds:.1f}")
        if code == EXIT_PODCASTS_PLAYING:
            log.info("not pushing %s: Apple Podcasts is playing on this Mac", job.track_id)
            return job.finish(False, "podcasts_playing")
        if code != 0:
            log.warning("podcasts-remote failed (%s): %s", code, out)
            return job.finish(False, "tool_failed")

        # Believe the library, not the tool: the library is what Podcasts syncs to iCloud.
        deadline = self._clock() + CONFIRM_WAIT
        while True:
            ep = self.db.episode(job.track_id)
            if ep and abs(ep.playhead - job.seconds) <= CONFIRM_TOLERANCE:
                log.info("pushed %s to Apple Podcasts at %s", job.track_id, handoff.fmt_time(job.seconds))
                return job.finish(True, "sent")
            if self._clock() >= deadline:
                log.warning("Apple Podcasts did not record %s for %s", handoff.fmt_time(job.seconds), job.track_id)
                return job.finish(False, "not_confirmed")
            self._sleep(0.2)


def attach(service, pusher: Pusher, toast_wait: float = TOAST_WAIT) -> None:
    """Wrap POST /progress: after the usual work (and the Shortcut link), push the spot."""
    progress = service.handlers["progress"]

    def progress_and_push(body: Dict) -> Dict:
        video_id = str(body.get("videoId") or "")
        cached = service.state.video(video_id) or {}
        prev = service.state.progress(cached["track_id"]) if cached.get("track_id") else None
        result = progress(body)  # validates the body, records progress, writes the Shortcut link
        if not result.get("matched"):
            return result
        entry = service.resolve(video_id)  # cached by the call above
        track_id = entry["track_id"]
        cur = service.state.progress(track_id) or {"time": body.get("currentTime"), "at": time.time()}
        moved = moved_at(prev, cur)
        service.state.set_progress(track_id, {**cur, "moved_at": moved})

        event = str(body.get("event") or "")
        if event not in PUSH_EVENTS:
            return result
        job = pusher.submit(track_id, entry["collection_id"], result["podcastTime"], moved)
        if event == "pause":  # the only event the extension shows a toast for
            job.done.wait(toast_wait)
        outcome = job.result or {"pushed": False, "reason": "pending"}
        result.update(pushed=outcome["pushed"], push=outcome["reason"])
        return result

    service.handlers["progress"] = progress_and_push
    service.status_extras.append(lambda: {"pushToPodcasts": {"enabled": True, "last": pusher.last}})


def register(service) -> None:
    tool = Path(service.cfg.state_dir) / TOOL_NAME
    if not service.cfg.push_to_podcasts:
        off = "turned off in config"
    elif os.environ.get("PODSYNC_FAKE_LIBRARY"):
        off = "fake library"  # end-to-end tests never touch the real Podcasts app
    elif not os.access(tool, os.X_OK):
        off = f"{tool} not found: run scripts/install.sh"
    else:
        off = None
    if off:
        service.status_extras.append(lambda: {"pushToPodcasts": {"enabled": False, "reason": off}})
        return
    attach(service, Pusher(service.db, service.app, tool))
