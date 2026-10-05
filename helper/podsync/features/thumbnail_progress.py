"""POST /known {videoIds: [...]}: Apple Podcasts progress for YouTube thumbnails.

The extension sends the video IDs it sees on a page (at most MAX_IDS). For each video
that the helper matched before (state.videos), it reads the episode from the Podcasts
library on this Mac and returns how far you are:

    {"<videoId>": {"fraction": 0.65, "label": "40:30 of 1:02:09", "lastPlayed": 1790000000.0}}

No network, no Podcasts launch, no new matching: unknown videos are left out.
Only episodes you started and did not finish are returned.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Dict

from .. import handoff
from ..service import BadRequest

log = logging.getLogger("podsync.known")

MAX_IDS = 60
MIN_STARTED = 30.0  # seconds, the same rule as the jump
FINISHED_LEFT = 60.0  # seconds before the end


def register(service) -> None:
    def known(body: Dict) -> Dict:
        ids = body.get("videoIds")
        if not isinstance(ids, list) or len(ids) > MAX_IDS:
            raise BadRequest(f"videoIds must be a list of at most {MAX_IDS}")
        if not all(isinstance(i, str) and re.fullmatch(r"[\w-]{11}", i) for i in ids):
            raise BadRequest("bad videoId")
        if getattr(service.db, "access", None) is False:
            return {}
        out: Dict[str, Dict] = {}
        episodes: Dict[int, object] = {}
        for video_id in dict.fromkeys(ids):  # unique, in order
            entry = service.state.video(video_id)
            track_id = entry.get("track_id") if entry else None
            if not track_id:
                continue
            if track_id not in episodes:
                try:
                    episodes[track_id] = service.db.episode(track_id)
                except sqlite3.Error as e:  # library busy or not readable: skip quietly
                    log.warning("library read failed: %s", e)
                    return out
            ep = episodes[track_id]
            if not ep or not ep.duration or ep.playhead < MIN_STARTED or ep.playhead > ep.duration - FINISHED_LEFT:
                continue
            out[video_id] = {
                "fraction": round(min(1.0, ep.playhead / ep.duration), 4),
                "label": f"{handoff.fmt_time(ep.playhead)} of {handoff.fmt_time(ep.duration)}",
                "lastPlayed": ep.last_played,
            }
        return out

    service.handlers["known"] = known
