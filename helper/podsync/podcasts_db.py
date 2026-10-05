"""Read-only access to the Apple Podcasts library on this Mac.

The Podcasts app keeps a Core Data store that iCloud keeps in sync with the
iPhone. We only ever open it with mode=ro.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import quote

APPLE_EPOCH = 978307200  # 2001-01-01 in unix time

_COLUMNS = """
    e.ZSTORETRACKID, p.ZSTORECOLLECTIONID, p.ZTITLE, e.ZTITLE,
    COALESCE(NULLIF(e.ZDURATION, 0), NULLIF(e.ZENTITLEDDURATION, 0), e.ZFREEDURATION),
    e.ZPUBDATE, e.ZPLAYHEAD, e.ZLASTDATEPLAYED
"""


@dataclass
class Episode:
    track_id: int
    collection_id: int
    show: str
    title: str
    duration: Optional[float]
    pub_date: Optional[float]  # unix seconds
    playhead: float
    last_played: Optional[float]  # unix seconds

    @classmethod
    def from_row(cls, r) -> "Episode":
        def unix(v):
            return v + APPLE_EPOCH if v else None

        return cls(
            track_id=int(r[0] or 0),
            collection_id=int(r[1] or 0),
            show=r[2] or "",
            title=r[3] or "",
            duration=float(r[4]) if r[4] else None,
            pub_date=unix(r[5]),
            playhead=float(r[6] or 0),
            last_played=unix(r[7]),
        )


def _try_open(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.read(16)
        return "ok"
    except OSError as e:
        return repr(e)


class PodcastsDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.access: Optional[bool] = None  # None until the first check finishes (it can wait on a macOS prompt)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{quote(str(self.path))}?mode=ro", uri=True, timeout=5)

    def activity(self):
        """Cheap signature that changes whenever the Podcasts app writes to its library."""
        sig = []
        for suffix in ("", "-wal"):
            try:
                st = os.stat(f"{self.path}{suffix}")
                sig.append((st.st_mtime_ns, st.st_size))
            except OSError:
                sig.append(None)
        return tuple(sig)

    def readable(self) -> bool:
        try:
            with self._connect() as c:
                c.execute("SELECT 1 FROM ZMTEPISODE LIMIT 1")
            self.access = True
        except sqlite3.Error as e:
            self.last_error = f"{e}; file open: {_try_open(self.path)}"
            self.access = False
        return self.access

    def episodes(self, collection_ids: Iterable[int]) -> List[Episode]:
        ids = [int(i) for i in collection_ids]
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        sql = f"""SELECT {_COLUMNS} FROM ZMTEPISODE e JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
                  WHERE p.ZSTORECOLLECTIONID IN ({marks}) AND e.ZSTORETRACKID > 0"""
        with self._connect() as c:
            return [Episode.from_row(r) for r in c.execute(sql, ids)]

    def episodes_near(self, unix_time: float, days: float = 10, recent_play_days: float = 90) -> List[Episode]:
        """Episodes published within `days` of a date, from shows you follow or played recently."""
        center = unix_time - APPLE_EPOCH
        played_since = time.time() - APPLE_EPOCH - recent_play_days * 86400
        sql = f"""SELECT {_COLUMNS} FROM ZMTEPISODE e JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
                  WHERE e.ZSTORETRACKID > 0 AND e.ZPUBDATE BETWEEN ? AND ?
                    AND (p.ZSUBSCRIBED = 1 OR EXISTS (
                         SELECT 1 FROM ZMTEPISODE x WHERE x.ZPODCAST = p.Z_PK AND x.ZLASTDATEPLAYED > ?))"""
        with self._connect() as c:
            rows = c.execute(sql, (center - days * 86400, center + days * 86400, played_since))
            return [Episode.from_row(r) for r in rows]

    def episode(self, track_id: int) -> Optional[Episode]:
        sql = f"""SELECT {_COLUMNS} FROM ZMTEPISODE e JOIN ZMTPODCAST p ON e.ZPODCAST = p.Z_PK
                  WHERE e.ZSTORETRACKID = ? LIMIT 1"""
        with self._connect() as c:
            row = c.execute(sql, (int(track_id),)).fetchone()
        return Episode.from_row(row) if row else None


class JsonLibrary:
    """Stand-in for the Podcasts library, loaded from a JSON list of episodes.

    Used by the end-to-end test (PODSYNC_FAKE_LIBRARY) so it runs on any Mac.
    Entries may give last_played_ago (seconds) instead of last_played.
    """

    def __init__(self, path: Path):
        now = time.time()
        rows = []
        for e in json.loads(Path(path).read_text()):
            e = dict(e)
            if "last_played_ago" in e:
                e["last_played"] = now - e.pop("last_played_ago")
            rows.append(Episode(**e))
        self.rows = {e.track_id: e for e in rows}
        self.path = Path(path)

    access = True

    def activity(self):
        return None

    def readable(self) -> bool:
        return True

    def episodes(self, collection_ids: Iterable[int]) -> List[Episode]:
        ids = set(collection_ids)
        return [e for e in self.rows.values() if e.collection_id in ids]

    def episodes_near(self, unix_time: float, days: float = 10, recent_play_days: float = 90) -> List[Episode]:
        return [e for e in self.rows.values() if e.pub_date and abs(e.pub_date - unix_time) <= days * 86400]

    def episode(self, track_id: int) -> Optional[Episode]:
        return self.rows.get(track_id)
