from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional


class State:
    """Small JSON store: video->episode matches and the last YouTube position per episode."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "state.json"
        self._lock = threading.Lock()
        try:
            self.data: Dict[str, Any] = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("videos", {})
        self.data.setdefault("progress", {})
        self.data.setdefault("channels", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path)

    def video(self, video_id: str) -> Optional[Dict]:
        with self._lock:
            return self.data["videos"].get(video_id)

    def set_video(self, video_id: str, entry: Dict) -> None:
        with self._lock:
            self.data["videos"][video_id] = entry
            self.save()

    def progress(self, track_id: int) -> Optional[Dict]:
        with self._lock:
            return self.data["progress"].get(str(track_id))

    def set_progress(self, track_id: int, entry: Dict) -> None:
        with self._lock:
            self.data["progress"][str(track_id)] = entry
            self.save()

    def channel_shows(self, channel_id: Optional[str]) -> list:
        with self._lock:
            return list(self.data["channels"].get(channel_id or "", []))

    def learn_channel(self, channel_id: str, collection_id: int) -> None:
        """Remember which podcast a YouTube channel publishes, after a confident match."""
        with self._lock:
            known = self.data["channels"].setdefault(channel_id, [])
            if collection_id not in known:
                known.append(collection_id)
                self.save()
