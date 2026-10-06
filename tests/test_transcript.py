"""Transcript alignment: TTML parsing, word normalizing, and the anchor map.

All transcripts here are synthetic: made-up words from a seeded generator.
"""
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync import transcript as T  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/transcript_sample.ttml"

COMMON = "the and you know so it is a to of that i we like".split()


def speech(n, seed=1, step=0.4):
    """n made-up words, one every `step` seconds from 0: [(seconds, word)]."""
    rng = random.Random(seed)
    syllables = ["ka", "lo", "mi", "ren", "tu", "sa", "vo", "pe", "dri", "nax", "ol", "be"]
    out = []
    for i in range(n):
        if rng.random() < 0.35:
            w = rng.choice(COMMON)
        else:
            w = "".join(rng.choice(syllables) for _ in range(rng.randint(2, 3)))
        out.append((i * step, w))
    return out


def episode(n=900, seed=1):
    """A DOAC-like pair (DOAC = the measured real case):

    audio: 30 s of an inserted ad, then the episode.
    video: the episode starts 7.3 s earlier than in the audio; at word n/2 the video
    has a 13.7 s ad read that the audio does not, so after it the video runs 6.4 s later.
    Returns (video_words, audio_words, jump_audio_time).
    """
    body = speech(n, seed)
    ad_audio = [(t, w) for t, w in speech(75, seed=seed + 100)]  # 30 s, other words
    audio = ad_audio + [(t + 30.0 + 7.3, w) for t, w in body]  # body starts at 37.3 s
    half = n // 2
    jump_at = body[half][0]
    video = [(t, w) for t, w in body[:half]]  # body starts at 0 s in the video
    video += [(jump_at + t, w) for t, w in speech(34, seed=seed + 200)]  # 13.7 s ad read
    video += [(t + 13.7, w) for t, w in body[half:]]
    return video, audio, jump_at + 37.3


def asr(words, seed=3, garble=0.1, drop=0.05):
    """Simulate caption errors: some words wrong, some missing, small time jitter."""
    rng = random.Random(seed)
    out = []
    for t, w in words:
        r = rng.random()
        if r < drop:
            continue
        if r < drop + garble:
            w = "zz" + w[::-1]
        out.append((max(0.0, t + rng.uniform(-0.25, 0.25)), w))
    return out


class ParseTest(unittest.TestCase):
    def test_time_formats(self):
        for text, want in (
            ("1.100", 1.1),
            ("1.1s", 1.1),
            ("500ms", 0.5),
            ("8:06.120", 486.12),
            ("1:08:06.120", 4086.12),
            ("0:00:02", 2.0),
        ):
            self.assertAlmostEqual(T.parse_time(text), want, msg=text)
        for bad in ("", "abc", "1:2:3:4", "1.5:00", "nan", "inf", "-1", "1e9", ":"):
            self.assertIsNone(T.parse_time(bad), bad)

    def test_ttml(self):
        words, dur = T.parse_ttml(FIXTURE.read_text())
        self.assertAlmostEqual(dur, 3665.25)
        self.assertEqual([w for _, w in words], ["Welcome", "back,", "I'm", "a", "made", "up", "show", "Q&A", "time."])
        times = [round(t, 3) for t, _ in words]
        # Word spans in all three time formats; a text-only span is spread over its range.
        self.assertEqual(times, [1.1, 1.14, 1.5, 2.0, 2.5, 3.0, 3.5, 598.5, 3658.25])

    def test_ttml_without_words_or_duration(self):
        self.assertEqual(T.parse_ttml("<tt><body><div></div></body></tt>"), ([], None))
        self.assertEqual(T.parse_ttml("not xml at all"), ([], None))

    def test_tokens(self):
        self.assertEqual(T.tokens("I'm"), ["im"])
        self.assertEqual(T.tokens("You’re"), ["youre"])
        self.assertEqual(T.tokens("well-known."), ["well", "known"])
        self.assertEqual(T.tokens("$4B"), ["4b"])
        self.assertEqual(T.tokens("Café"), ["café"])
        for noise in (">>", "[Music]", "[", "__", "]", "", "  ", "--"):
            self.assertEqual(T.tokens(noise), [], noise)


class AlignTest(unittest.TestCase):
    def assertMaps(self, amap, yt, audio, places=0.6):
        self.assertLess(abs(amap.to_audio(yt) - audio), places, f"yt {yt} -> {amap.to_audio(yt)}, want {audio}")
        self.assertLess(abs(amap.to_youtube(audio) - yt), places, f"audio {audio} -> {amap.to_youtube(audio)}, want {yt}")

    def test_constant_offset(self):
        body = speech(600)
        video = [(t + 2.0, w) for t, w in body]
        audio = [(t + 14.0, w) for t, w in body]
        amap, stats = T.build_map(video, audio)
        self.assertIsNotNone(amap, stats)
        self.assertEqual(amap.segments, 1)
        for t in (0, 50, 120.4, 239.0, 500):
            self.assertMaps(amap, t, t + 12.0, places=0.01)

    def test_mid_episode_jump(self):
        video, audio, jump_audio = episode()
        amap, stats = T.build_map(video, audio)
        self.assertIsNotNone(amap, stats)
        self.assertEqual(amap.segments, 2, stats)
        jump_yt = jump_audio - 37.3
        # Before the video's ad read: audio = video + 37.3 (30 s audio ad + 7.3 s).
        for t in (5.0, 60.0, jump_yt - 5):
            self.assertMaps(amap, t, t + 37.3)
        # After it: the video gained 13.7 s, so audio = video + 23.6.
        for t in (jump_yt + 20, jump_yt + 100, 370.0):
            self.assertMaps(amap, t, t + 23.6)
        # Before the first anchor: the first offset; the audio's own ad maps to the video start.
        self.assertMaps(amap, 0.0, 37.3)

    def test_no_interpolation_across_a_jump(self):
        video, audio, jump_audio = episode()
        amap, _ = T.build_map(video, audio)
        jump_yt = jump_audio - 37.3
        offsets = {round(amap.to_audio(t) - t, 1) for t in [jump_yt - 2 + i * 0.5 for i in range(40)]}
        self.assertLessEqual(offsets, {37.3, 23.6}, "no in-between offset inside the ad read")

    def test_caption_errors(self):
        video, audio, jump_audio = episode(seed=7)
        amap, stats = T.build_map(asr(video), audio)
        self.assertIsNotNone(amap, stats)
        self.assertEqual(amap.segments, 2, stats)
        jump_yt = jump_audio - 37.3
        self.assertMaps(amap, 40.0, 77.3, places=0.6)
        self.assertMaps(amap, jump_yt + 60, jump_yt + 83.6, places=0.6)

    def test_other_episode_does_not_match(self):
        amap, stats = T.build_map(speech(900, seed=1), speech(900, seed=2))
        self.assertIsNone(amap)
        self.assertEqual(stats["reason"], "too_few_matches")

    def test_partial_overlap_is_not_trusted(self):
        # Only the first minute of a 6-minute video matches (say, the wrong episode with the same intro).
        body = speech(900, seed=1)
        other = speech(900, seed=9)
        amap, stats = T.build_map(body[:150] + [(t + 60, w) for t, w in other[150:]], body)
        self.assertIsNone(amap)
        self.assertEqual(stats["reason"], "low_coverage")

    def test_too_few_words(self):
        amap, stats = T.build_map([(0.0, "hi")], speech(100))
        self.assertIsNone(amap)
        self.assertEqual(stats["reason"], "too_few_words")

    def test_anchors_are_sparse_and_ordered(self):
        video, audio, _ = episode()
        amap, _ = T.build_map(video, audio)
        self.assertLess(len(amap), 60)
        self.assertEqual(amap.yt, sorted(amap.yt))
        self.assertEqual(amap.audio, sorted(amap.audio))

    def test_json_round_trip(self):
        video, audio, _ = episode()
        amap, _ = T.build_map(video, audio)
        again = T.AnchorMap.from_json(amap.to_json())
        for t in (3.0, 200.0, 350.0):
            self.assertAlmostEqual(again.to_audio(t), amap.to_audio(t), places=1)
        for bad in ([], "x", [[1, 2]], [[1, 2, 0], [2, 1, 0]], [[1, 2, 1], [2, 3, 0]], [[1, float("nan"), 0]]):
            with self.assertRaises((ValueError, TypeError), msg=bad):
                T.AnchorMap.from_json(bad)


if __name__ == "__main__":
    unittest.main()
