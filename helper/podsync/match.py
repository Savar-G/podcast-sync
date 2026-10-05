"""Match a YouTube video to an Apple Podcasts episode.

Titles often differ between the two (YouTube titles are written for clicks),
so length and publish date carry most of the weight; shared title words
(usually the guest's name) break ties.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .podcasts_db import Episode
from .youtube import VideoMeta

STOPWORDS = set(
    """a an the and or but of to in on at for from by with without about into over under
    is are was were be been being it its it's this that these those you your yours we our
    i me my he his she her they them their what why how when where who whom which
    not no yes can can't will won't do does did done just more most very really
    dr ep episode podcast part full interview essentials s t""".split()
)

W_DURATION, W_DATE, W_TITLE = 0.45, 0.25, 0.30
# Perfect length + date alone scores 0.70, so a match also needs some shared title words.
ACCEPT_SCORE = 0.72
ACCEPT_MARGIN = 0.08
SURE_SCORE = 0.85
# Searching every show you follow (no known channel) needs more evidence. On real data the
# best wrong candidate scored 0.68; the weakest right one 0.81.
STRICT_SCORE = 0.78
STRICT_MARGIN = 0.10


@dataclass
class Match:
    episode: Episode
    score: float
    runner_up: float


def tokens(text: str) -> set:
    text = text.lower().replace("’", "'").replace("‘", "'")
    words = re.findall(r"[a-z0-9$][a-z0-9'$.]*", text)
    out = set()
    for w in words:
        w = w.strip("'.").removesuffix("'s")
        if len(w) > 2 and w not in STOPWORDS:
            out.add(w)
    return out


def title_score(a: str, b: str, show: str = "") -> float:
    noise = tokens(show)  # "Huberman Lab Essentials: ..." should not match on the show's own name
    ta, tb = tokens(a) - noise, tokens(b) - noise
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


AUDIO_AD_ALLOWANCE = 300.0  # audio feeds often carry a few minutes of ads the video lacks


def duration_score(video: Optional[float], episode: Optional[float]) -> float:
    if not video or not episode:
        return 0.5
    tolerance = max(90.0, 0.03 * episode)
    extra_audio = episode - video
    if extra_audio > 0:
        ad_part = min(extra_audio, AUDIO_AD_ALLOWANCE)
        miss = extra_audio - ad_part
        return max(0.0, 1.0 - 0.1 * ad_part / AUDIO_AD_ALLOWANCE - miss / (3 * tolerance))
    return max(0.0, 1.0 + extra_audio / (3 * tolerance))


def date_score(video: Optional[float], episode: Optional[float]) -> float:
    if not video or not episode:
        return 0.5
    days = abs(video - episode) / 86400
    return max(0.0, 1.0 - days / 10)


def score(meta: VideoMeta, ep: Episode) -> float:
    return (
        W_DURATION * duration_score(meta.duration, ep.duration)
        + W_DATE * date_score(meta.published, ep.pub_date)
        + W_TITLE * title_score(meta.title, ep.title, ep.show)
    )


def best_match(meta: VideoMeta, candidates: Sequence[Episode], strict: bool = False) -> Optional[Match]:
    ranked: List[tuple] = sorted(((score(meta, ep), ep) for ep in candidates), key=lambda x: -x[0])
    if not ranked:
        return None
    top, ep = ranked[0]
    second = ranked[1][0] if len(ranked) > 1 else 0.0
    if strict:
        ok = top >= STRICT_SCORE and top - second >= STRICT_MARGIN
    else:
        ok = top >= SURE_SCORE or (top >= ACCEPT_SCORE and top - second >= ACCEPT_MARGIN)
    return Match(episode=ep, score=round(top, 3), runner_up=round(second, 3)) if ok else None
