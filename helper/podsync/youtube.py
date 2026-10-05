"""Fetch the facts we need about a YouTube video from its public watch page.

The extension only sends a video id; the helper looks up channel, length and
publish date itself so matching does not depend on YouTube's DOM.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "CONSENT=YES+1",
}


@dataclass
class VideoMeta:
    video_id: str
    title: str
    channel_id: Optional[str] = None
    channel: Optional[str] = None
    duration: Optional[float] = None
    published: Optional[float] = None  # unix seconds

    def to_dict(self):
        return asdict(self)


def parse_watch_page(video_id: str, html: str) -> Optional[VideoMeta]:
    start = html.find("ytInitialPlayerResponse")
    if start < 0:
        return None
    brace = html.find("{", start)
    try:
        data, _ = json.JSONDecoder().raw_decode(html[brace:])
    except ValueError:
        return None
    details = data.get("videoDetails") or {}
    micro = (data.get("microformat") or {}).get("playerMicroformatRenderer") or {}
    published = None
    for key in ("publishDate", "uploadDate"):
        if micro.get(key):
            published = _parse_date(micro[key])
            if published:
                break
    length = details.get("lengthSeconds") or micro.get("lengthSeconds")
    return VideoMeta(
        video_id=video_id,
        title=details.get("title") or (micro.get("title") or {}).get("simpleText") or "",
        channel_id=details.get("channelId") or micro.get("externalChannelId"),
        channel=details.get("author") or micro.get("ownerChannelName"),
        duration=float(length) if length else None,
        published=published,
    )


def _parse_date(s: str) -> Optional[float]:
    s = s.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        s += "T12:00:00+00:00"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def fetch(video_id: str, timeout: float = 10) -> Optional[VideoMeta]:
    if not re.fullmatch(r"[\w-]{11}", video_id or ""):
        return None
    req = urllib.request.Request(f"https://www.youtube.com/watch?v={video_id}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        html = r.read().decode("utf-8", "ignore")
    return parse_watch_page(video_id, html)
