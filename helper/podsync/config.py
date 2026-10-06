"""Settings. Everything has a default, so a config file is optional.

Looked up in order: $PODSYNC_CONFIG, ~/.config/podcast-sync/config.json.
See config.example.json for the format.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

HOME = Path.home()

PODCASTS_DB = HOME / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite"
# Word-timed transcripts that Apple Podcasts downloaded (features/transcript_anchoring.py). Read-only.
PODCASTS_TTML = HOME / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Library/Cache/Assets/TTML"
# Files here sync to the iPhone, and the Shortcut's "Get File" action reads paths relative to it.
SHORTCUTS_ICLOUD_DIR = HOME / "Library/Mobile Documents/iCloud~is~workflow~my~workflows/Documents"
DEFAULT_HANDOFF_DIR = SHORTCUTS_ICLOUD_DIR / "podcast-sync"
DEFAULT_STATE_DIR = HOME / "Library/Application Support/podcast-sync"
DEFAULT_CONFIG = HOME / ".config/podcast-sync/config.json"

# The extension's manifest pins its public key, so its ID is the same on every machine.
EXTENSION_ID = "jmjijpbkkfcdipfedjjjakoeoignkkcm"


@dataclass
class Show:
    """Optional per-show override. Shows are found automatically; use this to pin a
    YouTube channel to a podcast or to fix a constant time offset."""

    name: str
    youtube_channels: List[str] = field(default_factory=list)
    apple_ids: List[int] = field(default_factory=list)
    offset_seconds: float = 0.0  # YouTube time minus Podcasts time


@dataclass
class Config:
    port: int = 47321
    min_video_seconds: int = 600
    podcasts_idle_quit_seconds: int = 600
    # On pause, also move Apple Podcasts on this Mac to the YouTube spot; iCloud then
    # carries it to the iPhone, so no Shortcut tap is needed (features/push_to_podcasts.py).
    push_to_podcasts: bool = True
    extension_ids: List[str] = field(default_factory=lambda: [EXTENSION_ID])
    shows: List[Show] = field(default_factory=list)
    db_path: Path = PODCASTS_DB
    transcripts_dir: Path = PODCASTS_TTML
    handoff_dir: Path = DEFAULT_HANDOFF_DIR
    state_dir: Path = DEFAULT_STATE_DIR

    def show_for_channel(self, channel_id: Optional[str]) -> Optional[Show]:
        for s in self.shows:
            if channel_id and channel_id in s.youtube_channels:
                return s
        return None

    def show_for_apple_id(self, apple_id: int) -> Optional[Show]:
        for s in self.shows:
            if apple_id in s.apple_ids:
                return s
        return None


def load(path: Optional[Path] = None) -> Config:
    path = Path(path or os.environ.get("PODSYNC_CONFIG") or DEFAULT_CONFIG)
    raw: Dict = json.loads(path.read_text()) if path.exists() else {}
    shows = [Show(**s) for s in raw.pop("shows", [])]
    cfg = Config(shows=shows, **{k: v for k, v in raw.items() if k in Config.__dataclass_fields__ and k != "shows"})
    for env, attr in (("PODSYNC_HANDOFF_DIR", "handoff_dir"), ("PODSYNC_STATE_DIR", "state_dir"), ("PODSYNC_TRANSCRIPTS_DIR", "transcripts_dir")):
        if os.environ.get(env):
            setattr(cfg, attr, Path(os.environ[env]))
    if os.environ.get("PODSYNC_PORT"):
        cfg.port = int(os.environ["PODSYNC_PORT"])
    return cfg
