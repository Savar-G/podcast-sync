"""Fallback episode list from Apple's public lookup API.

Used when a brand-new episode is not in the Mac library yet, so the
YouTube -> iPhone link still works.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from .podcasts_db import Episode

_cache: Dict[int, Tuple[float, List[Episode]]] = {}
TTL = 1800


def episodes(collection_ids: Iterable[int], timeout: float = 10) -> List[Episode]:
    out: List[Episode] = []
    for cid in collection_ids:
        hit = _cache.get(cid)
        if hit and time.time() - hit[0] < TTL:
            out += hit[1]
            continue
        url = f"https://itunes.apple.com/lookup?id={cid}&media=podcast&entity=podcastEpisode&limit=50"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                results = json.load(r).get("results", [])
        except Exception:
            continue
        eps = [_episode(x) for x in results if x.get("kind") == "podcast-episode" and x.get("trackId")]
        _cache[cid] = (time.time(), eps)
        out += eps
    return out


def _episode(x: Dict) -> Episode:
    pub = None
    if x.get("releaseDate"):
        try:
            pub = datetime.fromisoformat(x["releaseDate"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    ms = x.get("trackTimeMillis")
    return Episode(
        track_id=int(x["trackId"]),
        collection_id=int(x.get("collectionId") or 0),
        show=x.get("collectionName") or "",
        title=x.get("trackName") or "",
        duration=ms / 1000 if ms else None,
        pub_date=pub,
        playhead=0.0,
        last_played=None,
    )
