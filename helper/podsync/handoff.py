"""Write the 'resume here' link that the iPhone Shortcut opens."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Dict


def podcast_url(collection_id: int, track_id: int, seconds: float, scheme: str = "podcasts") -> str:
    return f"{scheme}://podcasts.apple.com/podcast/id{collection_id}?i={track_id}&t={max(0, int(seconds))}"


def fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(tmp, 0o644)  # mkstemp makes 0600; keep the iCloud copy readable
    os.replace(tmp, path)


def write(handoff_dir: Path, *, collection_id: int, track_id: int, seconds: float, show: str, episode: str, video_id: str) -> Dict:
    info = {
        "url": podcast_url(collection_id, track_id, seconds),
        "web_url": podcast_url(collection_id, track_id, seconds, scheme="https"),
        "seconds": int(seconds),
        "time": fmt_time(seconds),
        "show": show,
        "episode": episode,
        "youtube": f"https://www.youtube.com/watch?v={video_id}&t={int(seconds)}s",
        "saved_at": int(time.time()),
    }
    _atomic_write(handoff_dir / "resume.json", json.dumps(info, indent=1, ensure_ascii=False))
    _atomic_write(handoff_dir / "resume.txt", info["url"] + "\n")
    return info
