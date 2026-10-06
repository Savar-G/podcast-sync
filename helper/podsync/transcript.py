"""Line up a YouTube video with its Apple Podcasts audio by their transcripts.

The video and the audio of one episode often differ by an intro or by ads, and
the difference can change in the middle (an ad read in one, not in the other).
One constant offset per show cannot follow that. Both sides have word-timed text:

  * Apple Podcasts caches a TTML transcript for many episodes on this Mac;
  * the YouTube player has captions (auto-generated ones have word times).

build_map() finds word sequences (n-grams) that occur exactly once in each
transcript, keeps the ones in a consistent order, groups them into segments of
a steady offset, and returns an AnchorMap: a short list of
(youtube_time, audio_time) pairs. Nothing else is kept: no text.

Mapping inside a segment interpolates between its anchors. Between two segments
(an ad boundary) it uses the offset of the nearest anchor and never interpolates
across the jump.
"""
from __future__ import annotations

import bisect
import html
import math
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Word = Tuple[float, str]  # (seconds, text)
Anchor = Tuple[float, float, int]  # (youtube seconds, audio seconds, segment)

NGRAM = 4
SEGMENT_TOLERANCE = 2.0  # s: an offset change larger than this starts a new segment
RECENT = 5  # anchors: a segment's current offset is the median of its last few
MIN_SEGMENT = 4  # anchors: a shorter run is noise (a phrase that happens to match)
SPACING = 15.0  # s of YouTube time between the anchors we keep
MIN_ANCHORS = 20  # matched n-grams needed before we trust a map
MIN_COVERAGE = 0.5  # share of the minutes of the video with at least one match
COVERAGE_WINDOW = 60.0

_SPAN = re.compile(r"<span\b([^>]*)>([^<]*)</span>")
_ATTR = re.compile(r"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_BODY = re.compile(r"<body\b([^>]*)>")
_APOSTROPHE = re.compile(r"['’‘`]")
_NON_WORD = re.compile(r"[\W_]+")


# ---- TTML (Apple Podcasts) -------------------------------------------------
def parse_time(value: str) -> Optional[float]:
    """TTML clock value: '1.100', '1.1s', '8:06.120' or '1:08:06.120' -> seconds."""
    s = (value or "").strip()
    if s.endswith("ms"):
        scale, s = 0.001, s[:-2]
    elif s.endswith("s"):
        scale, s = 1.0, s[:-1]
    else:
        scale = 1.0
    parts = s.split(":")
    if not s or len(parts) > 3:
        return None
    total = 0.0
    for i, p in enumerate(parts):
        if not re.fullmatch(r"\d+(\.\d+)?", p) or (i < len(parts) - 1 and "." in p):
            return None
        total = total * 60 + float(p)
    total *= scale
    return total if math.isfinite(total) else None


def _attrs(raw: str) -> Dict[str, str]:
    return {k: a if a or not b else b for k, a, b in _ATTR.findall(raw)}


def parse_ttml(text: str) -> Tuple[List[Word], Optional[float]]:
    """Words with start times, and the body's duration (None if missing).

    Apple writes <span begin=".." end=".." podcasts:unit="word">He</span> inside
    sentence spans. A text span with several words (no word timing) spreads them
    evenly over its time range.
    """
    words: List[Word] = []
    for m in _SPAN.finditer(text):
        a = _attrs(m.group(1))
        begin = parse_time(a.get("begin", ""))
        if begin is None:
            continue
        tokens = html.unescape(m.group(2)).split()
        end = parse_time(a.get("end", ""))
        words.extend(_spread(begin, end, tokens))
    words.sort(key=lambda w: w[0])
    body = _BODY.search(text)
    dur = parse_time(_attrs(body.group(1)).get("dur", "")) if body else None
    return words, dur


def _spread(begin: float, end: Optional[float], tokens: Sequence[str]) -> List[Word]:
    if len(tokens) <= 1 or end is None or end <= begin:
        return [(begin, t) for t in tokens]
    step = (end - begin) / len(tokens)
    return [(begin + i * step, t) for i, t in enumerate(tokens)]


# ---- words -----------------------------------------------------------------
def tokens(word: str) -> List[str]:
    """'I'm' -> ['im'], 'well-known' -> ['well', 'known']; '>>', '[Music]', '[ __ ]' -> []."""
    w = (word or "").strip().lower()
    if not w or (w.startswith("[") and w.endswith("]")):
        return []
    return [t for t in _NON_WORD.split(_APOSTROPHE.sub("", w)) if t]


def normalize(words: Iterable[Word]) -> Tuple[List[float], List[str]]:
    times, toks = [], []
    for t, w in words:
        for tok in tokens(w):
            times.append(float(t))
            toks.append(tok)
    return times, toks


def _unique_grams(toks: Sequence[str], n: int) -> Dict[tuple, int]:
    """n-gram -> start index, for n-grams that occur once (-1 marks repeats)."""
    out: Dict[tuple, int] = {}
    for i in range(len(toks) - n + 1):
        g = tuple(toks[i : i + n])
        out[g] = -1 if g in out else i
    return out


def _increasing(pairs: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Longest run of pairs (sorted by the first index) whose second index also increases."""
    tails: List[int] = []  # tails[k] = smallest second index ending a run of length k+1
    tail_at: List[int] = []  # position in pairs of that tail
    prev = [-1] * len(pairs)
    for p, (_, j) in enumerate(pairs):
        k = bisect.bisect_left(tails, j)
        if k == len(tails):
            tails.append(j)
            tail_at.append(p)
        else:
            tails[k] = j
            tail_at[k] = p
        prev[p] = tail_at[k - 1] if k else -1
    out = []
    p = tail_at[-1] if tail_at else -1
    while p >= 0:
        out.append(pairs[p])
        p = prev[p]
    return out[::-1]


def _median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# ---- the map -----------------------------------------------------------------
class AnchorMap:
    """(youtube_time, audio_time, segment) pairs, in order on both sides."""

    def __init__(self, anchors: Iterable[Sequence[float]]):
        pts = sorted((float(a[0]), float(a[1]), int(a[2])) for a in anchors)
        if not pts:
            raise ValueError("no anchors")
        for a, b in zip(pts, pts[1:]):
            if b[1] < a[1] or b[2] < a[2]:
                raise ValueError("anchors out of order")
        if not all(math.isfinite(x) for p in pts for x in p[:2]):
            raise ValueError("bad anchor")
        self.yt = [p[0] for p in pts]
        self.audio = [p[1] for p in pts]
        self.seg = [p[2] for p in pts]

    def __len__(self) -> int:
        return len(self.yt)

    @property
    def segments(self) -> int:
        return len(set(self.seg))

    def to_audio(self, youtube_time: float) -> float:
        return self._map(self.yt, self.audio, youtube_time)

    def to_youtube(self, audio_time: float) -> float:
        return self._map(self.audio, self.yt, audio_time)

    def _map(self, src: List[float], dst: List[float], t: float) -> float:
        i = bisect.bisect_right(src, t)
        if i == 0:
            return t + dst[0] - src[0]
        if i == len(src):
            return t + dst[-1] - src[-1]
        a, b = i - 1, i
        off_a, off_b = dst[a] - src[a], dst[b] - src[b]
        if self.seg[a] == self.seg[b] and src[b] > src[a]:
            return t + off_a + (t - src[a]) / (src[b] - src[a]) * (off_b - off_a)
        # Between two segments: an ad or intro in one version only. Do not interpolate.
        return t + (off_a if t - src[a] <= src[b] - t else off_b)

    def to_json(self) -> List[List[float]]:
        return [[round(y, 2), round(a, 2), s] for y, a, s in zip(self.yt, self.audio, self.seg)]

    @classmethod
    def from_json(cls, data) -> "AnchorMap":
        if not isinstance(data, list) or not all(isinstance(p, list) and len(p) == 3 for p in data):
            raise ValueError("bad anchors")
        return cls(data)


def build_map(youtube: Iterable[Word], apple: Iterable[Word], n: int = NGRAM) -> Tuple[Optional[AnchorMap], Dict]:
    """Align YouTube captions with an Apple transcript. Returns (map or None, stats)."""
    yt_t, yt_w = normalize(youtube)
    ap_t, ap_w = normalize(apple)
    stats: Dict = {"youtubeWords": len(yt_w), "appleWords": len(ap_w)}
    if len(yt_w) < n or len(ap_w) < n:
        return None, {**stats, "reason": "too_few_words"}

    # 1. n-grams found exactly once on each side: the same moment in both.
    ap_grams = _unique_grams(ap_w, n)
    pairs = sorted((i, ap_grams[g]) for g, i in _unique_grams(yt_w, n).items() if i >= 0 and ap_grams.get(g, -1) >= 0)
    # 2. Same order on both sides (drops a phrase matched far away).
    pairs = _increasing(pairs)
    stats["matches"] = len(pairs)

    # 3. (youtube time, audio - youtube); the median over the n words smooths word-time jitter.
    raw = [(yt_t[i], _median([ap_t[j + k] - yt_t[i + k] for k in range(n)])) for i, j in pairs]

    # 4. Segments of a steady offset; short ones are chance matches.
    segs: List[List[Tuple[float, float]]] = []
    for y, d in raw:
        if segs and abs(d - _median([o for _, o in segs[-1][-RECENT:]])) <= SEGMENT_TOLERANCE:
            segs[-1].append((y, d))
        else:
            segs.append([(y, d)])
    kept: List[List[Tuple[float, float]]] = []
    for s in (s for s in segs if len(s) >= MIN_SEGMENT):
        if kept and abs(_median([o for _, o in kept[-1][-RECENT:]]) - _median([o for _, o in s[:RECENT]])) <= SEGMENT_TOLERANCE:
            kept[-1].extend(s)  # an outlier had split one segment in two
        else:
            kept.append(s)

    good = sum(len(s) for s in kept)
    span = (yt_t[0], yt_t[-1])
    windows = max(1, int(math.ceil((span[1] - span[0]) / COVERAGE_WINDOW)))
    covered = {int((y - span[0]) // COVERAGE_WINDOW) for s in kept for y, _ in s}
    coverage = min(1.0, len(covered) / windows)
    stats.update(anchored=good, coverage=round(coverage, 3), segments=len(kept))
    if good < MIN_ANCHORS:
        return None, {**stats, "reason": "too_few_matches"}
    if coverage < MIN_COVERAGE:
        return None, {**stats, "reason": "low_coverage"}

    # 5. Keep about one anchor per SPACING seconds, plus each segment's ends (the jump edges).
    anchors: List[Anchor] = []
    for k, s in enumerate(kept):
        buckets: Dict[int, List[Tuple[float, float]]] = {}
        for y, d in s:
            buckets.setdefault(int(y // SPACING), []).append((y, d))
        picks = {s[0], s[-1]}
        for group in buckets.values():
            mid = _median([d for _, d in group])
            picks.add(min(group, key=lambda p: (abs(p[1] - mid), p[0])))
        anchors.extend((y, y + d, k) for y, d in sorted(picks))
    # Audio time must not go back (word-time jitter at a segment edge).
    clean: List[Anchor] = []
    for a in anchors:
        if not clean or (a[0] > clean[-1][0] and a[1] >= clean[-1][1]):
            clean.append(a)
    amap = AnchorMap(clean)
    stats["anchors"] = len(amap)
    return amap, stats
