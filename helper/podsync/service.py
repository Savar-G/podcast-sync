"""The sync rules, independent of HTTP.

Newest wins: a position only moves to the other side if it is newer than the
last position we saw from that side.
"""
from __future__ import annotations

import logging
import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import apple_lookup, handoff, match
from .config import Config, Show
from .podcasts_app import PodcastsApp
from .podcasts_db import PodcastsDB
from .state import State
from .youtube import VideoMeta, fetch

log = logging.getLogger("podsync")

MATCH_TTL = 7 * 86400
NO_MATCH_TTL = 3600
WRITE_EVENTS = {"pause", "hidden", "unload", "navigate", "ended", "seeked"}
HEARTBEAT_WRITE_SECONDS = 20
MIN_SEEK_DELTA = 15


class BadRequest(ValueError):
    """Raised by a handler when the request body is invalid (HTTP 400)."""


def video_args(body: Dict[str, Any]) -> Tuple[str, float, Dict]:
    """Validate the common {videoId, currentTime, title, duration} body."""
    try:
        video_id = str(body["videoId"])
        current = float(body.get("currentTime") or 0)
    except (KeyError, TypeError, ValueError):
        raise BadRequest("videoId and currentTime required")
    if not re.fullmatch(r"[\w-]{11}", video_id) or not 0 <= current < 86400:
        raise BadRequest("bad videoId or currentTime")
    return video_id, current, {"title": body.get("title"), "duration": body.get("duration")}


class SyncService:
    def __init__(
        self,
        cfg: Config,
        db: PodcastsDB,
        app: PodcastsApp,
        state: State,
        *,
        fetch_meta: Callable[[str], Optional[VideoMeta]] = fetch,
        lookup: Callable = apple_lookup.episodes,
    ):
        self.cfg, self.db, self.app, self.state = cfg, db, app, state
        self.fetch_meta, self.lookup = fetch_meta, lookup
        self._last_write: Dict = {}
        # POST /<name> -> handler(body). Features in podsync/features add their own.
        self.handlers: Dict[str, Callable[[Dict], Dict]] = {
            "match": lambda b: self.match(*_drop_time(video_args(b))),
            "resume": lambda b: self.resume(*video_args(b)),
            "progress": lambda b: self.progress(*_with_event(video_args(b), b)),
        }
        self.status_extras: List[Callable[[], Dict]] = []
        # fn(collection_id) -> seconds, added to the configured offset (e.g. a learned one).
        self.offset_extras: List[Callable[[int], float]] = []
        # Objects with to_podcast(video_id, track_id, youtube_time) and
        # to_youtube(video_id, track_id, podcast_time), each returning seconds or None.
        # The first answer wins; with none, the show's constant offset is used.
        self.position_mappers: List[Any] = []
        # POST /<name> -> largest body in bytes, for the few endpoints that need more than the default.
        self.body_limits: Dict[str, int] = {}
        from . import features

        features.load_all(self)

    # ---- matching -------------------------------------------------------
    def resolve(self, video_id: str, hint: Optional[Dict] = None) -> Dict:
        cached = self.state.video(video_id)
        if cached:
            ttl = MATCH_TTL if cached.get("track_id") else NO_MATCH_TTL
            if time.time() - cached.get("at", 0) < ttl:
                return cached

        meta = None
        try:
            meta = self.fetch_meta(video_id)
        except Exception as e:  # offline, YouTube changed, etc.
            log.warning("YouTube lookup failed for %s: %s", video_id, e)
        if meta is None and hint:
            meta = VideoMeta(video_id=video_id, title=hint.get("title") or "", duration=hint.get("duration"))
        if meta is None:
            return {"reason": "no_video_info"}  # not cached: retry next time

        entry = self._match(meta)
        entry.update(at=time.time(), video={"title": meta.title, "channel": meta.channel, "duration": meta.duration})
        self.state.set_video(video_id, entry)
        return entry

    def _match(self, meta: VideoMeta) -> Dict:
        if meta.duration and meta.duration < self.cfg.min_video_seconds:
            return {"reason": "short_video"}

        # 1. A show you pinned in config, or one this channel matched before: search all its episodes.
        show = self.cfg.show_for_channel(meta.channel_id)
        apple_ids = show.apple_ids if show else self.state.channel_shows(meta.channel_id)
        m, source = None, ""
        if apple_ids:
            m = match.best_match(meta, self.db.episodes(apple_ids))
            source = "library"
            if m is None:  # brand-new episode the Mac library has not fetched yet
                m = match.best_match(meta, self.lookup(apple_ids))
                source = "apple_lookup"
        # 2. Unknown channel: search shows you follow or played lately, around the upload date.
        elif meta.published:
            m = match.best_match(meta, self.db.episodes_near(meta.published), strict=True)
            source = "auto"
            if m and meta.channel_id:
                self.state.learn_channel(meta.channel_id, m.episode.collection_id)
        if m is None:
            return {"reason": "no_matching_episode"}
        ep = m.episode
        log.info("matched %r -> %r (%.2f, %s)", meta.title, ep.title, m.score, source)
        return {
            "track_id": ep.track_id,
            "collection_id": ep.collection_id,
            "episode": ep.title,
            "show": show.name if show else ep.show,
            "score": m.score,
            "source": source,
        }

    def _offset(self, collection_id: int) -> float:
        """YouTube time minus Podcasts time: the configured offset plus any learned one."""
        show: Optional[Show] = self.cfg.show_for_apple_id(collection_id)
        offset = float(show.offset_seconds) if show else 0.0
        for extra in self.offset_extras:
            offset += extra(collection_id)
        return offset

    def _convert(self, direction: str, video_id: Optional[str], track_id: int, collection_id: int, t: float) -> Tuple[float, str]:
        """Map a position between the video and the audio: (seconds, "transcript" | "offset")."""
        if video_id:
            for mapper in self.position_mappers:
                try:
                    got = getattr(mapper, direction)(video_id, track_id, t)
                except Exception:
                    log.exception("position mapper failed")
                    continue
                if got is not None and math.isfinite(got):
                    return float(got), getattr(mapper, "name", "map")
        offset = self._offset(collection_id)
        return (t + offset if direction == "to_youtube" else t - offset), "offset"

    def to_youtube_time(self, video_id: Optional[str], track_id: int, collection_id: int, podcast_time: float) -> float:
        return self._convert("to_youtube", video_id, track_id, collection_id, podcast_time)[0]

    def to_podcast_time(self, video_id: Optional[str], track_id: int, collection_id: int, youtube_time: float) -> float:
        return self._convert("to_podcast", video_id, track_id, collection_id, youtube_time)[0]

    def match(self, video_id: str, hint: Optional[Dict] = None) -> Dict:
        entry = self.resolve(video_id, hint)
        if not entry.get("track_id"):
            return {"matched": False, "reason": entry.get("reason", "no_match")}
        return {"matched": True, "episode": entry["episode"], "show": entry["show"], "score": entry.get("score")}

    # ---- Apple Podcasts -> YouTube --------------------------------------
    def resume(self, video_id: str, current_time: float, hint: Optional[Dict] = None) -> Dict:
        entry = self.resolve(video_id, hint)
        if not entry.get("track_id"):
            return {"action": "none", "reason": entry.get("reason", "no_match")}
        track_id = entry["track_id"]
        youtube_last = (self.state.progress(track_id) or {}).get("at", 0)

        ep = self.app.refresh(track_id)
        if ep is None:
            return {"action": "none", "reason": "not_in_library", "episode": entry["episode"]}
        target, sync = self._convert("to_youtube", video_id, track_id, ep.collection_id, ep.playhead)
        base = {"episode": ep.title, "show": entry["show"], "podcastTime": ep.playhead, "lastPlayed": ep.last_played, "sync": sync}
        video_len = (entry.get("video") or {}).get("duration")
        if video_len:
            target = min(target, video_len - 5)
        target = max(0.0, target)
        started = ep.playhead >= 30
        finished = bool(ep.duration and ep.playhead > ep.duration - 60)
        if started and not finished:  # where the iPhone is, in video time, for the progress-bar marker
            base.update(markerTime=target, markerLabel=handoff.fmt_time(target))
        if not ep.last_played or ep.last_played <= youtube_last:
            return {"action": "none", "reason": "youtube_is_newer", **base}
        if not started:
            return {"action": "none", "reason": "podcast_not_started", **base}
        if finished:
            return {"action": "none", "reason": "podcast_finished", **base}

        if abs(target - current_time) < MIN_SEEK_DELTA:
            return {"action": "none", "reason": "already_there", **base}
        log.info("resume %s at %s (Podcasts played %s)", video_id, handoff.fmt_time(target), ep.last_played)
        return {"action": "seek", "time": target, "label": handoff.fmt_time(target), **base}

    # ---- YouTube -> Apple Podcasts --------------------------------------
    def progress(self, video_id: str, current_time: float, event: str, hint: Optional[Dict] = None) -> Dict:
        entry = self.resolve(video_id, hint)
        if not entry.get("track_id"):
            return {"matched": False, "reason": entry.get("reason", "no_match")}
        track_id, collection_id = entry["track_id"], entry["collection_id"]
        now = time.time()
        self.state.set_progress(track_id, {"time": current_time, "at": now, "video_id": video_id})

        podcast_time, sync = self._convert("to_podcast", video_id, track_id, collection_id, current_time)
        podcast_time = max(0.0, podcast_time)
        result = {
            "matched": True,
            "sync": sync,
            "episode": entry["episode"],
            "show": entry["show"],
            "podcastTime": podcast_time,
            "label": handoff.fmt_time(podcast_time),
            "url": handoff.podcast_url(collection_id, track_id, podcast_time),
        }
        last = self._last_write
        due = event in WRITE_EVENTS or now - last.get("at", 0) >= HEARTBEAT_WRITE_SECONDS
        changed = (last.get("track_id"), last.get("seconds")) != (track_id, int(podcast_time))
        if due and changed:
            handoff.write(
                self.cfg.handoff_dir,
                collection_id=collection_id,
                track_id=track_id,
                seconds=podcast_time,
                show=entry["show"],
                episode=entry["episode"],
                video_id=video_id,
            )
            self._last_write = {"at": now, "track_id": track_id, "seconds": int(podcast_time)}
            result["saved"] = True
        return result

    def status(self) -> Dict:
        last = None
        try:
            import json

            last = json.loads((self.cfg.handoff_dir / "resume.json").read_text())
        except (OSError, ValueError):
            pass
        # Never block here: the first library check may be waiting on a macOS prompt.
        out = {"ok": True, "libraryReadable": getattr(self.db, "access", None), "lastHandoff": last}
        for extra in self.status_extras:
            try:
                out.update(extra())
            except Exception:
                log.exception("status extra failed")
        return out


def _drop_time(args):
    video_id, _current, hint = args
    return video_id, hint


def _with_event(args, body):
    video_id, current, hint = args
    return video_id, current, str(body.get("event") or ""), hint
