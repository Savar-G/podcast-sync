"""POST /nudge {videoId, delta}: learn a show's offset from the -15 s / +15 s buttons.

The offset is YouTube time minus Podcasts time. If a jump lands 15 s too early in the
video, you press +15 s: the video runs 15 s ahead of the audio, so the show's learned
offset grows by 15 s. Each press counts once, and the sum per show (collection_id) is
kept in state.data["offsets"], clamped to +-MAX_LEARNED. service._offset() adds it to the
configured offset, so it applies both ways: the YouTube jump and the iPhone link.

When the video is lined up by transcript (features/transcript_anchoring.py), that
feature takes the press instead and keeps it for the episode, not the show.
"""
from __future__ import annotations

import math
import re
from typing import Dict, Tuple

from ..service import BadRequest

MAX_LEARNED = 600.0  # seconds, either way
MAX_DELTA = 60.0  # one press


def parse_nudge(body: Dict) -> Tuple[str, float]:
    """Validate a {videoId, delta} body. Shared with transcript_anchoring's per-episode nudge."""
    video_id = str(body.get("videoId") or "")
    if not re.fullmatch(r"[\w-]{11}", video_id):
        raise BadRequest("bad videoId")
    try:
        delta = float(body["delta"])
    except (KeyError, TypeError, ValueError):
        raise BadRequest("delta required")
    if not math.isfinite(delta) or delta == 0 or abs(delta) > MAX_DELTA:
        raise BadRequest(f"delta must be non-zero, at most {MAX_DELTA:g} s")
    return video_id, delta


def register(service) -> None:
    state = service.state

    def learned(collection_id: int) -> float:
        return float(state.data.get("offsets", {}).get(str(collection_id), 0.0))

    def nudge(body: Dict) -> Dict:
        video_id, delta = parse_nudge(body)
        entry = state.video(video_id)  # only videos we matched before: no network here
        if not entry or not entry.get("collection_id"):
            return {"saved": False, "reason": "not_matched"}
        key = str(entry["collection_id"])
        with state._lock:
            offsets = state.data.setdefault("offsets", {})
            raw = float(offsets.get(key, 0.0)) + delta
            value = round(max(-MAX_LEARNED, min(MAX_LEARNED, raw)), 1)
            offsets[key] = value
            state.save()
        return {
            "saved": True,
            "scope": "show",
            "show": entry.get("show"),
            "learned": value,
            "offset": service._offset(entry["collection_id"]),
            "clamped": value != round(raw, 1),
        }

    service.offset_extras.append(learned)
    service.handlers["nudge"] = nudge
