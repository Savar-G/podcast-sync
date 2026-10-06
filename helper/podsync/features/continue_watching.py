"""Continue on YouTube: POST /recent lists the episodes you played lately in
Apple Podcasts, each with a link to its YouTube video at your position.

Finding the video, in order:
  1. a video the helper already matched to the episode (state.videos);
  2. new uploads on a YouTube channel known for the show (learned channels and
     config), from the channel's public RSS feed, confirmed with service.resolve();
  3. otherwise a YouTube search link.

Network work runs in threads under a time budget, so the popup stays fast. Work
that misses the budget keeps running and fills the match cache for next time.

Body: {} or {"refresh": true}. With refresh, the helper first lets the Podcasts
app pull iCloud play state once (for the newest episode), which takes a few
seconds; the popup asks for that only after it shows the fast answer.

"hidden": {"<trackId>": <lastPlayed unix>} lists episodes you hid in the popup.
They stay out of the list until you play them again (a newer lastPlayed), and
the next recent episodes fill their places. The popup keeps this list itself.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor, wait
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urlparse

from .. import handoff, match
from ..podcasts_db import Episode
from ..service import BadRequest
from ..youtube import _parse_date

log = logging.getLogger("podsync.recent")

RECENT_DAYS = 14
MIN_PLAYHEAD = 60
END_MARGIN = 60
MAX_ROWS = 5
MAX_HIDDEN = 200  # entries accepted in one request; the popup prunes old ones
CANDIDATE_DAYS = 10  # uploads this close to the episode's publish date
MAX_CANDIDATES = 6  # per episode, best first
BUDGET_SECONDS = 6.0
FEED_TTL = 30 * 60
FEED_ERROR_TTL = 5 * 60
NOT_FOUND_TTL = 60 * 60  # the YouTube upload can come a day after the podcast
FEED_TIMEOUT = 4.0
MAX_FEED_BYTES = 2 * 1024 * 1024
ARTWORK_SIZE = 120  # px; shown at 48 px, so this is sharp on 2x screens

FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
_VIDEO_ID = re.compile(r"[\w-]{11}")
_CHANNEL_ID = re.compile(r"UC[\w-]{22}")

Upload = Tuple[str, str, Optional[float]]  # (video_id, title, published unix)


def parse_feed(xml: bytes) -> List[Upload]:
    """Uploads in a YouTube channel feed. Shorts are left out."""
    out = []
    for entry in ET.fromstring(xml).findall("a:entry", _NS):
        vid = entry.findtext("yt:videoId", "", _NS)
        link = entry.find("a:link", _NS)
        if not _VIDEO_ID.fullmatch(vid) or (link is not None and "/shorts/" in link.get("href", "")):
            continue
        published = entry.findtext("a:published", "", _NS)
        out.append((vid, entry.findtext("a:title", "", _NS), _parse_date(published) if published else None))
    return out


def fetch_feed(channel_id: str, timeout: float = FEED_TIMEOUT) -> List[Upload]:
    req = urllib.request.Request(FEED_URL.format(channel_id), headers={"User-Agent": "podcast-sync"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return parse_feed(r.read(MAX_FEED_BYTES))


def artwork_url(template: Optional[str], size: int = ARTWORK_SIZE) -> Optional[str]:
    """Turn the library's Apple image template into a small JPEG URL. Apple's image host only."""
    if not template:
        return None
    u = urlparse(template)
    if u.scheme != "https" or not (u.hostname or "").endswith(".mzstatic.com"):
        return None
    return template.replace("{w}", str(size)).replace("{h}", str(size)).replace("{c}", "bb").replace("{f}", "jpg")


def parse_hidden(raw) -> Dict[int, float]:
    """{"<trackId>": lastPlayed} from the popup, validated. Missing means nothing is hidden."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > MAX_HIDDEN:
        raise BadRequest(f"hidden must be an object with at most {MAX_HIDDEN} entries")
    out = {}
    for key, last_played in raw.items():
        if not (isinstance(key, str) and key.isdigit() and len(key) <= 20):
            raise BadRequest("hidden keys must be track ids")
        if isinstance(last_played, bool) or not isinstance(last_played, (int, float)) or not math.isfinite(last_played):
            raise BadRequest("hidden values must be numbers")
        out[int(key)] = float(last_played)
    return out


def is_hidden(ep: Episode, hidden: Dict[int, float]) -> bool:
    """Hidden until you play the episode again: a lastPlayed newer than when you hid it."""
    if ep.track_id not in hidden:
        return False
    return (ep.last_played or 0) <= hidden[ep.track_id] + 1  # 1 s of slack for float round trips


def search_url(show: str, title: str) -> str:
    return "https://www.youtube.com/results?search_query=" + quote_plus(f"{show} {title}".strip())


def watch_url(video_id: str, seconds: float) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&t={max(0, int(seconds))}s"


def rank_uploads(ep: Episode, uploads: Sequence[Upload], days: float = CANDIDATE_DAYS) -> List[str]:
    """Uploads near the episode's publish date, most likely first (shared title words, then date)."""
    near = []
    for vid, title, published in uploads:
        if ep.pub_date and published and abs(published - ep.pub_date) > days * 86400:
            continue
        gap = abs(published - ep.pub_date) if ep.pub_date and published else days * 86400
        near.append((-match.title_score(title, ep.title, ep.show), gap, vid))
    seen, out = set(), []
    for _, _, vid in sorted(near):
        if vid not in seen:
            seen.add(vid)
            out.append(vid)
    return out


class ContinueWatching:
    def __init__(
        self,
        service,
        *,
        fetch_feed: Callable[[str], List[Upload]] = fetch_feed,
        clock: Callable[[], float] = time.time,
        budget: float = BUDGET_SECONDS,
        workers: int = 8,
    ):
        self.service = service
        self.fetch_feed, self.clock, self.budget = fetch_feed, clock, budget
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="podsync-recent")
        self._lock = threading.RLock()  # a done-callback can run inside _submit
        self._feeds: Dict[str, Tuple[float, Optional[List[Upload]]]] = {}  # channel -> (expires, uploads or None on error)
        self._not_found: Dict[int, float] = {}  # track_id -> expires
        self._inflight: Dict[tuple, Future] = {}  # shared by concurrent requests

    # ---- HTTP -------------------------------------------------------------
    def handle(self, body: Dict) -> Dict:
        refresh = body.get("refresh", False)
        if not isinstance(refresh, bool):
            raise BadRequest("refresh must be true or false")
        return self.recent(refresh=refresh, hidden=parse_hidden(body.get("hidden")))

    def recent(self, refresh: bool = False, hidden: Optional[Dict[int, float]] = None) -> Dict:
        db = self.service.db
        since = self.clock() - RECENT_DAYS * 86400
        hidden = hidden or {}
        # Ask for extra rows, so hidden episodes leave room for the next ones.
        limit = MAX_ROWS + min(len(hidden), 20)

        def visible() -> List[Episode]:
            rows = db.recently_played(since, MIN_PLAYHEAD, END_MARGIN, limit)
            return [e for e in rows if not is_hidden(e, hidden)][:MAX_ROWS]

        try:
            eps = visible()
        except Exception as e:  # sqlite3.Error, or macOS has not allowed access yet
            log.warning("cannot list recent episodes: %s", e)
            return {"episodes": [], "error": "library_unreadable"}
        refreshed = False
        if refresh and eps:
            # One refresh only: it waits for the Podcasts app to finish its iCloud sync, which
            # updates every episode, not just this one. Per-row refreshes would take minutes.
            try:
                self.service.app.refresh(eps[0].track_id)
                eps = visible()
                refreshed = True
            except Exception as e:
                log.warning("Podcasts refresh failed: %s", e)
        videos = self.find_videos(eps)
        return {"episodes": [self.row(ep, videos.get(ep.track_id)) for ep in eps], "refreshed": refreshed}

    # ---- rows -------------------------------------------------------------
    def row(self, ep: Episode, video: Optional[Tuple[str, Optional[float]]]) -> Dict:
        video_id, video_len = video or (None, None)
        youtube_time = max(0.0, self.service.to_youtube_time(video_id, ep.track_id, ep.collection_id, ep.playhead))
        if video_len:
            youtube_time = max(0.0, min(youtube_time, video_len - 5))
        return {
            "trackId": ep.track_id,
            "collectionId": ep.collection_id,
            "show": ep.show,
            "episode": ep.title,
            "playhead": ep.playhead,
            "duration": ep.duration,
            "lastPlayed": ep.last_played,
            "youtubeTime": youtube_time,
            "label": handoff.fmt_time(youtube_time),
            "videoId": video_id,
            "url": watch_url(video_id, youtube_time) if video_id else search_url(ep.show, ep.title),
            "artwork": artwork_url(ep.artwork),
        }

    # ---- finding the video ------------------------------------------------
    def find_videos(self, eps: Sequence[Episode]) -> Dict[int, Tuple[str, Optional[float]]]:
        """track_id -> (video_id, video length) for each episode whose video we can find in time."""
        deadline = self.clock() + self.budget
        found = {}
        todo = []
        now = self.clock()
        for ep in eps:
            hit = self.cached_video(ep.track_id)
            if hit:
                found[ep.track_id] = hit
            elif self._not_found.get(ep.track_id, 0) <= now:
                todo.append(ep)
        if not todo:
            return found

        # Phase 1: channel feeds, all in parallel.
        channels = {ep.track_id: self.channels_for(ep.collection_id) for ep in todo}
        todo = [ep for ep in todo if channels[ep.track_id]]
        feed_futs = {ch: self._feed(ch) for ep in todo for ch in channels[ep.track_id]}
        self._wait(feed_futs.values(), deadline)

        # Phase 2: confirm the likeliest uploads with the matcher, all in parallel.
        plans: Dict[int, Tuple[List[Future], bool]] = {}
        for ep in todo:
            uploads, feeds_ok = [], True
            for ch in channels[ep.track_id]:
                f = feed_futs[ch]
                got = f.result() if f.done() and not f.exception() else None
                if got is None:  # failed or still loading
                    feeds_ok = False
                else:
                    uploads.extend(got)
            cands = rank_uploads(ep, uploads)[:MAX_CANDIDATES]
            plans[ep.track_id] = ([self._resolve(v) for v in cands], feeds_ok)
        self._wait([f for futs, _ in plans.values() for f in futs], deadline)

        for ep in todo:
            futs, complete = plans[ep.track_id]
            for f in futs:
                if not f.done():
                    complete = False
                    continue
                if f.exception():
                    complete = False
                    continue
                vid, entry = f.result()
                if entry.get("track_id") == ep.track_id:
                    found[ep.track_id] = (vid, (entry.get("video") or {}).get("duration"))
                    break
            else:
                if complete:  # every check finished and none matched: do not look again for a while
                    self._not_found[ep.track_id] = self.clock() + NOT_FOUND_TTL
        return found

    def cached_video(self, track_id: int) -> Optional[Tuple[str, Optional[float]]]:
        """The video you last watched for this episode, else the newest cached match for it."""
        state = self.service.state
        videos = dict(state.data.get("videos", {}))
        last = (state.progress(track_id) or {}).get("video_id")
        if last and (videos.get(last) or {}).get("track_id") == track_id:
            return last, (videos[last].get("video") or {}).get("duration")
        hits = [(e.get("at", 0), vid, e) for vid, e in videos.items() if e.get("track_id") == track_id]
        if not hits:
            return None
        _, vid, e = max(hits, key=lambda h: h[0])
        return vid, (e.get("video") or {}).get("duration")

    def channels_for(self, collection_id: int) -> List[str]:
        """YouTube channels known to publish this show: from config, then learned from matches."""
        out = []
        for show in self.service.cfg.shows:
            if collection_id in show.apple_ids:
                out.extend(show.youtube_channels)
        learned = dict(self.service.state.data.get("channels", {}))
        out.extend(ch for ch, ids in learned.items() if collection_id in ids)
        return [ch for i, ch in enumerate(out) if _CHANNEL_ID.fullmatch(ch or "") and ch not in out[:i]]

    # ---- shared, cached background work ----------------------------------
    def _feed(self, channel_id: str) -> Future:
        with self._lock:
            cached = self._feeds.get(channel_id)
            if cached and cached[0] > self.clock():
                f: Future = Future()
                f.set_result(cached[1])
                return f
        return self._submit(("feed", channel_id), self._load_feed, channel_id)

    def _load_feed(self, channel_id: str) -> Optional[List[Upload]]:
        uploads: Optional[List[Upload]]
        try:
            uploads, ttl = self.fetch_feed(channel_id), FEED_TTL
        except Exception as e:
            log.warning("YouTube feed failed for %s: %s", channel_id, e)
            uploads, ttl = None, FEED_ERROR_TTL
        with self._lock:
            self._feeds[channel_id] = (self.clock() + ttl, uploads)
        return uploads

    def _resolve(self, video_id: str) -> Future:
        return self._submit(("resolve", video_id), lambda: (video_id, self.service.resolve(video_id)))

    def _submit(self, key: tuple, fn: Callable, *args) -> Future:
        with self._lock:
            f = self._inflight.get(key)
            if f is None:
                f = self._pool.submit(fn, *args)
                self._inflight[key] = f
                f.add_done_callback(lambda _f, k=key: self._forget(k, _f))
            return f

    def _forget(self, key: tuple, f: Future) -> None:
        with self._lock:
            if self._inflight.get(key) is f:
                del self._inflight[key]

    def _wait(self, futs, deadline: float) -> None:
        futs = list(futs)
        if futs:
            wait(futs, timeout=max(0.0, deadline - self.clock()))


def register(service) -> None:
    feature = ContinueWatching(service)
    service.continue_watching = feature
    service.handlers["recent"] = feature.handle
