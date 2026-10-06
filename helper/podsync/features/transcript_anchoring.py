"""Line up YouTube and Apple Podcasts by transcript, when Apple has one.

    POST /transcript {videoId}
        -> {"need": bool, "anchored": bool, "reason": str}
        "need" asks the extension to send this video's captions, once.
    POST /captions {videoId, words: [[seconds, "word"], ...]}
        -> {"anchored": bool, "anchors": n, "segments": n, "approx": bool, "reason": str}
        An empty list means "this video has no captions": not asked again for a day.

Apple side: Apple Podcasts caches a word-timed TTML transcript for many episodes on
this Mac (config.transcripts_dir), named transcript_<storeTrackId>.ttml-<storeTrackId>.ttml.
The helper only reads it. transcript.build_map() aligns it with the captions.

When a map exists for (video, episode), it replaces the show's constant offset
in service._convert(): the jump, the iPhone link and push, and Continue on YouTube.
The show offset is not added on top. A -15 s / +15 s press then means your audio
file differs from Apple's transcript (dynamically inserted ads), so it is kept for
that episode only. An episode whose transcript length differs from the library's
duration by more than APPROX_GAP is marked "approx" for the same reason.

Privacy: captions and the transcript are held in memory for one request only.
Only the anchor pairs (numbers) are saved, in anchors.json in the state folder.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .. import transcript
from ..service import BadRequest
from .offset_learning import parse_nudge

log = logging.getLogger("podsync.transcript")

MAX_BODY = 2 * 1024 * 1024  # POST /captions only; ~15k words take ~300 KB
MAX_WORDS = 60000  # a 5-hour episode
MAX_WORD_CHARS = 100
MAX_TTML_BYTES = 8 * 1024 * 1024  # real files are ~1 MB per hour
MAX_MAPS = 60  # oldest maps are dropped first
NO_MAP_TTL = 86400  # an upload that gave no map is not asked for again for a day
INDEX_TTL = 300  # s: the folder listing is reused this long
MISS_RESCAN = 30  # s: a track that is not listed triggers a new listing at most this often
APPROX_GAP = 2.0  # s: transcript length vs. the library's duration
MAX_CORRECTION = 600.0  # s, either way, per episode

_FILE = re.compile(r"transcript_(\d+)\.ttml")
_VIDEO_ID = re.compile(r"[\w-]{11}")


def video_id_of(body: Dict) -> str:
    video_id = body.get("videoId")
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        raise BadRequest("bad videoId")
    return video_id


def words_of(raw) -> List[transcript.Word]:
    """Validate [[seconds, "word"], ...] from the extension."""
    if not isinstance(raw, list) or len(raw) > MAX_WORDS:
        raise BadRequest(f"words must be a list of at most {MAX_WORDS}")
    out = []
    for item in raw:
        if not isinstance(item, list) or len(item) != 2:
            raise BadRequest("words must be [seconds, text] pairs")
        t, w = item
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not 0 <= t < 86400:
            raise BadRequest("bad word time")
        if not isinstance(w, str) or len(w) > MAX_WORD_CHARS:
            raise BadRequest(f"words must be text of at most {MAX_WORD_CHARS} characters")
        out.append((float(t), w))
    out.sort(key=lambda x: x[0])
    return out


class TranscriptFiles:
    """Finds Apple's cached TTML file for an episode. Read-only."""

    def __init__(self, root: Optional[Path], clock: Callable[[], float] = time.time):
        self.root = Path(root) if root else None
        self.clock = clock
        self.readable: Optional[bool] = None  # None: not listed yet, or no such folder
        self._index: Dict[int, Path] = {}
        self._at = float("-inf")
        self._warned = False
        self._lock = threading.Lock()

    def path(self, track_id: int) -> Optional[Path]:
        if self.root is None:
            return None
        with self._lock:
            age = self.clock() - self._at
            if age > INDEX_TTL or (int(track_id) not in self._index and age > MISS_RESCAN):
                self._scan()
                self._at = self.clock()
            return self._index.get(int(track_id))

    def _scan(self) -> None:
        errors: List[OSError] = []
        index: Dict[int, Path] = {}
        for folder, _dirs, files in os.walk(self.root, onerror=errors.append):
            for name in files:
                m = _FILE.match(name)
                if m:
                    index[int(m.group(1))] = Path(folder) / name
        denied = any(isinstance(e, PermissionError) for e in errors)
        if denied and not index:
            self.readable = False
            self._warn("cannot read Apple Podcasts transcripts in %s: macOS did not allow it", self.root)
        else:
            self.readable = True if index or not errors else None
        self._index = index

    def read(self, path: Path) -> Optional[str]:
        try:
            if path.stat().st_size > MAX_TTML_BYTES:
                return None
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self._warn("cannot read transcript %s: %s", path.name, e)
            return None

    def _warn(self, msg: str, *args) -> None:
        if not self._warned:  # once: this repeats on every video otherwise
            self._warned = True
            log.warning(msg, *args)


class AnchorStore:
    """anchors.json: {"maps": {"<videoId>:<trackId>": {...}}, "corrections": {"<trackId>": seconds}}."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._data: Optional[Dict] = None
        self._parsed: Dict[str, Optional[transcript.AnchorMap]] = {}

    @staticmethod
    def key(video_id: str, track_id: int) -> str:
        return f"{video_id}:{int(track_id)}"

    def _load(self) -> Dict:
        if self._data is None:
            try:
                data = json.loads(self.path.read_text())
                if not isinstance(data, dict):
                    raise ValueError
            except (OSError, ValueError):
                data = {}
            for name in ("maps", "corrections"):
                if not isinstance(data.get(name), dict):
                    data[name] = {}
            self._data = data
        return self._data

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._load(), separators=(",", ":")))
        os.replace(tmp, self.path)

    def get(self, video_id: str, track_id: int) -> Optional[Dict]:
        with self._lock:
            entry = self._load()["maps"].get(self.key(video_id, track_id))
            return dict(entry) if isinstance(entry, dict) else None

    def amap(self, video_id: str, track_id: int) -> Optional[transcript.AnchorMap]:
        key = self.key(video_id, track_id)
        with self._lock:
            if key not in self._parsed:
                entry = self._load()["maps"].get(key)
                amap = None
                if isinstance(entry, dict) and entry.get("anchors"):
                    try:
                        amap = transcript.AnchorMap.from_json(entry["anchors"])
                    except (ValueError, TypeError, IndexError):
                        log.warning("ignoring a damaged anchor map for %s", key)
                self._parsed[key] = amap
            return self._parsed[key]

    def put(self, video_id: str, track_id: int, entry: Dict) -> None:
        key = self.key(video_id, track_id)
        with self._lock:
            maps = self._load()["maps"]
            maps.pop(key, None)
            maps[key] = {**entry, "at": self.clock()}
            for old in sorted(maps, key=lambda k: maps[k].get("at", 0))[: max(0, len(maps) - MAX_MAPS)]:
                del maps[old]
                self._parsed.pop(old, None)
            self._parsed.pop(key, None)
            self._save()

    def count(self) -> int:
        with self._lock:
            return sum(1 for e in self._load()["maps"].values() if isinstance(e, dict) and e.get("anchors"))

    def correction(self, track_id: int) -> float:
        with self._lock:
            try:
                return float(self._load()["corrections"].get(str(int(track_id)), 0.0))
            except (TypeError, ValueError):
                return 0.0

    def add_correction(self, track_id: int, delta: float) -> Tuple[float, bool]:
        with self._lock:
            raw = self.correction(track_id) + delta
            value = round(max(-MAX_CORRECTION, min(MAX_CORRECTION, raw)), 1)
            self._load()["corrections"][str(int(track_id))] = value
            self._save()
            return value, value != round(raw, 1)


class Anchoring:
    """The feature: HTTP handlers, the position mapper, and the per-episode nudge."""

    name = "transcript"  # service._convert() reports this as the "sync" source

    def __init__(self, service, files: TranscriptFiles, store: AnchorStore, clock: Callable[[], float] = time.time):
        self.service, self.files, self.store, self.clock = service, files, store, clock

    def _matched(self, video_id: str) -> Optional[Dict]:
        entry = self.service.state.video(video_id)  # only videos matched before: no network here
        return entry if entry and entry.get("track_id") else None

    # ---- HTTP ---------------------------------------------------------------
    def status_of(self, body: Dict) -> Dict:
        video_id = video_id_of(body)
        entry = self._matched(video_id)
        if not entry:
            return {"need": False, "anchored": False, "reason": "not_matched"}
        track_id = entry["track_id"]
        saved = self.store.get(video_id, track_id)
        if saved and saved.get("anchors"):
            return {"need": False, "anchored": True, "approx": bool(saved.get("approx"))}
        if saved and saved.get("failed") and self.clock() - saved.get("at", 0) < NO_MAP_TTL:
            return {"need": False, "anchored": False, "reason": saved["failed"]}
        if self.files.path(track_id) is None:
            return {"need": False, "anchored": False, "reason": "no_transcript"}
        return {"need": True, "anchored": False}

    def captions(self, body: Dict) -> Dict:
        video_id = video_id_of(body)
        words = words_of(body.get("words"))
        entry = self._matched(video_id)
        if not entry:
            return {"anchored": False, "reason": "not_matched"}
        track_id = entry["track_id"]
        if not words:
            self.store.put(video_id, track_id, {"failed": "no_captions"})
            return {"anchored": False, "reason": "no_captions"}
        path = self.files.path(track_id)
        text = self.files.read(path) if path else None
        if text is None:
            return {"anchored": False, "reason": "no_transcript"}
        apple, duration = transcript.parse_ttml(text)
        del text
        amap, stats = transcript.build_map(words, apple)
        if amap is None:
            log.info("no transcript match for %s (%s): %s", video_id, track_id, stats)
            self.store.put(video_id, track_id, {"failed": stats.get("reason", "no_match")})
            return {"anchored": False, "reason": stats.get("reason", "no_match")}
        approx = self._approx(track_id, duration)
        self.store.put(
            video_id,
            track_id,
            {"anchors": amap.to_json(), "approx": approx, "coverage": stats.get("coverage"), "segments": amap.segments},
        )
        log.info(
            "lined up %s with episode %s by transcript: %d anchors, %d segment(s)%s",
            video_id, track_id, len(amap), amap.segments, ", approximate" if approx else "",
        )
        return {"anchored": True, "anchors": len(amap), "segments": amap.segments, "approx": approx}

    def _approx(self, track_id: int, duration: Optional[float]) -> bool:
        """Apple's transcript is for a file of another length (ads inserted into yours)."""
        try:
            ep = self.service.db.episode(track_id)
        except Exception:  # sqlite3.Error or no access yet
            ep = None
        return bool(duration and ep and ep.duration and abs(duration - ep.duration) > APPROX_GAP)

    # ---- position mapper (service.position_mappers) ------------------------------
    def to_podcast(self, video_id: str, track_id: int, youtube_time: float) -> Optional[float]:
        amap = self.store.amap(video_id, track_id)
        return None if amap is None else amap.to_audio(youtube_time - self.store.correction(track_id))

    def to_youtube(self, video_id: str, track_id: int, podcast_time: float) -> Optional[float]:
        amap = self.store.amap(video_id, track_id)
        return None if amap is None else amap.to_youtube(podcast_time) + self.store.correction(track_id)

    # ---- -15 s / +15 s ------------------------------------------------------------
    def wrap_nudge(self, show_nudge: Callable[[Dict], Dict]) -> Callable[[Dict], Dict]:
        def nudge(body: Dict) -> Dict:
            video_id, delta = parse_nudge(body)
            entry = self._matched(video_id)
            if not entry or self.store.amap(video_id, entry["track_id"]) is None:
                return show_nudge(body)  # no map: the show's offset, as before
            value, clamped = self.store.add_correction(entry["track_id"], delta)
            return {"saved": True, "scope": "episode", "show": entry.get("show"), "correction": value, "clamped": clamped}

        return nudge

    def status(self) -> Dict:
        if self.files.root is None:
            folder = "off"
        elif self.files.readable is None:
            folder = "not_checked" if self.files._at == float("-inf") else "not_found"
        else:
            folder = "readable" if self.files.readable else "not_allowed"
        return {"transcripts": {"folder": folder, "maps": self.store.count()}}


def register(service) -> None:
    root = getattr(service.cfg, "transcripts_dir", None)
    if os.environ.get("PODSYNC_FAKE_LIBRARY") and not os.environ.get("PODSYNC_TRANSCRIPTS_DIR"):
        root = None  # end-to-end tests never read your real transcripts
    feature = Anchoring(service, TranscriptFiles(root), AnchorStore(Path(service.cfg.state_dir) / "anchors.json"))
    service.transcripts = feature
    service.handlers["transcript"] = feature.status_of
    service.handlers["captions"] = feature.captions
    service.body_limits["captions"] = MAX_BODY
    service.position_mappers.append(feature)
    if "nudge" in service.handlers:
        service.handlers["nudge"] = feature.wrap_nudge(service.handlers["nudge"])
    service.status_extras.append(feature.status)
