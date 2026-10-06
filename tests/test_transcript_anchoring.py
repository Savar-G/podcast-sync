"""Transcript anchoring as a feature: the endpoints, the mapping in resume and
progress, the per-episode nudge, and what is saved. Synthetic transcripts only."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
sys.path.insert(0, str(ROOT / "tests"))

import test_service  # noqa: E402
from podsync.features import transcript_anchoring as ta  # noqa: E402
from podsync.service import BadRequest  # noqa: E402
from podsync.state import State  # noqa: E402
from test_service import COLLECTION, TRACK  # noqa: E402
from test_transcript import episode as synthetic_episode  # noqa: E402
from test_transcript import speech  # noqa: E402

VID = "abcdefghijk"
JUMP_AUDIO = synthetic_episode()[2]
JUMP_YT = JUMP_AUDIO - 37.3


def fmt(t):
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:06.3f}"


def ttml(words, dur):
    """A TTML file like Apple's, from (seconds, word) pairs."""
    spans = "".join(f'<span begin="{fmt(t)}" end="{fmt(t + 0.3)}" podcasts:unit="word">{w}</span>' for t, w in words)
    return (
        '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal">'
        f'<body dur="{dur:.3f}"><div><p><span podcasts:unit="sentence">{spans}</span></p></div></body></tt>'
    )


class AnchoringTest(unittest.TestCase):
    _service = test_service.ServiceTest._service

    def setUp(self):
        test_service.ServiceTest.setUp(self)
        self.video_words, self.audio_words, _ = synthetic_episode()
        self.ttml_dir = self.cfg.transcripts_dir
        self.svc = self._service()
        self.db.rows[TRACK] = test_service.episode(playhead=300.0)  # iPhone at 5:00 of the audio

    def write_ttml(self, dur=3729.0, track=TRACK):  # the library's duration: the same audio file
        folder = self.ttml_dir / "PodcastContent1/v4/ab/cd"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"transcript_{track}.ttml-{track}.ttml").write_text(ttml(self.audio_words, dur))

    def call(self, name, body):
        return self.svc.handlers[name](body)

    def anchor(self, dur=3729.0):
        self.write_ttml(dur)
        self.svc.match(VID)
        return self.call("captions", {"videoId": VID, "words": [list(w) for w in self.video_words]})

    # ---- the endpoints -------------------------------------------------------
    def test_unmatched_video_needs_nothing(self):
        self.assertEqual(self.call("transcript", {"videoId": VID})["reason"], "not_matched")

    def test_no_transcript_on_this_mac(self):
        self.svc.match(VID)
        r = self.call("transcript", {"videoId": VID})
        self.assertEqual((r["need"], r["reason"]), (False, "no_transcript"))

    def test_captions_are_asked_for_once(self):
        self.write_ttml()
        self.svc.match(VID)
        self.assertTrue(self.call("transcript", {"videoId": VID})["need"])
        r = self.call("captions", {"videoId": VID, "words": [list(w) for w in self.video_words]})
        self.assertEqual((r["anchored"], r["segments"], r["approx"]), (True, 2, False))
        r = self.call("transcript", {"videoId": VID})
        self.assertEqual((r["need"], r["anchored"]), (False, True))

    def test_a_transcript_added_later_is_found(self):
        self.svc.match(VID)
        self.assertEqual(self.call("transcript", {"videoId": VID})["reason"], "no_transcript")
        self.write_ttml()
        self.svc.transcripts.files._at -= ta.MISS_RESCAN + 1
        self.assertTrue(self.call("transcript", {"videoId": VID})["need"])

    def test_no_captions_or_no_match_is_not_asked_again(self):
        self.write_ttml()
        self.svc.match(VID)
        r = self.call("captions", {"videoId": VID, "words": []})
        self.assertEqual(r["reason"], "no_captions")
        self.assertEqual(self.call("transcript", {"videoId": VID})["reason"], "no_captions")
        other = [[t, w] for t, w in speech(900, seed=42)]  # captions of another episode
        self.assertEqual(self.call("captions", {"videoId": VID, "words": other})["reason"], "too_few_matches")
        self.assertFalse(self.call("transcript", {"videoId": VID})["need"])
        self.svc.transcripts.clock = lambda: time.time() + ta.NO_MAP_TTL + 1  # a day later
        self.assertTrue(self.call("transcript", {"videoId": VID})["need"])

    def test_bad_captions_are_refused(self):
        self.write_ttml()
        self.svc.match(VID)
        for body in (
            {"words": []},
            {"videoId": "../../etc/pw", "words": []},
            {"videoId": VID},
            {"videoId": VID, "words": "hello"},
            {"videoId": VID, "words": [[1.0]]},
            {"videoId": VID, "words": [[1.0, "a", 2]]},
            {"videoId": VID, "words": [{"t": 1, "w": "a"}]},
            {"videoId": VID, "words": [["1.0", "a"]]},
            {"videoId": VID, "words": [[True, "a"]]},
            {"videoId": VID, "words": [[float("nan"), "a"]]},
            {"videoId": VID, "words": [[float("inf"), "a"]]},
            {"videoId": VID, "words": [[-1, "a"]]},
            {"videoId": VID, "words": [[86400, "a"]]},
            {"videoId": VID, "words": [[1.0, 5]]},
            {"videoId": VID, "words": [[1.0, "x" * (ta.MAX_WORD_CHARS + 1)]]},
            {"videoId": VID, "words": [[1.0, "a"]] * (ta.MAX_WORDS + 1)},
        ):
            with self.assertRaises(BadRequest, msg=str(body)[:80]):
                self.call("captions", body)
        with self.assertRaises(BadRequest):
            self.call("transcript", {"videoId": 12345678901})

    # ---- the mapping -------------------------------------------------------------
    def test_resume_uses_the_map_and_not_the_show_offset(self):
        for s in self.cfg.shows:
            s.offset_seconds = 90  # would double count if it were added on top
        self.anchor()
        r = self.svc.resume(VID, 0)
        # iPhone at 300 s of audio; after the video's ad read, audio = video + 23.6.
        self.assertEqual((r["action"], r["sync"]), ("seek", "transcript"))
        self.assertAlmostEqual(r["time"], 300 - 23.6, delta=0.6)
        self.assertAlmostEqual(r["markerTime"], r["time"])

    def test_progress_uses_the_map_both_sides_of_the_jump(self):
        for s in self.cfg.shows:
            s.offset_seconds = 90
        self.anchor()
        p = self.svc.progress(VID, 60, "pause")
        self.assertEqual(p["sync"], "transcript")
        self.assertAlmostEqual(p["podcastTime"], 97.3, delta=0.6)
        p = self.svc.progress(VID, JUMP_YT + 100, "pause")
        self.assertAlmostEqual(p["podcastTime"], JUMP_YT + 123.6, delta=0.6)
        info = json.loads((self.cfg.handoff_dir / "resume.json").read_text())
        self.assertAlmostEqual(info["seconds"], JUMP_YT + 123.6, delta=1)

    def test_without_a_map_the_show_offset_is_used(self):
        for s in self.cfg.shows:
            s.offset_seconds = 90
        self.svc.match(VID)
        r = self.svc.resume(VID, 0)
        self.assertEqual((r["time"], r["sync"]), (390, "offset"))
        self.assertEqual(self.svc.progress(VID, 1000, "pause")["podcastTime"], 910)

    def test_map_is_per_video(self):
        self.anchor()
        self.assertIsNone(self.svc.transcripts.to_podcast("zzzzzzzzzzz", TRACK, 60))
        self.assertIsNone(self.svc.transcripts.to_youtube(VID, TRACK + 1, 60))

    def test_continue_watching_uses_the_map(self):
        self.anchor()
        row = self.svc.continue_watching.row(self.db.rows[TRACK], (VID, 400.0))
        self.assertAlmostEqual(row["youtubeTime"], 300 - 23.6, delta=0.6)
        row = self.svc.continue_watching.row(self.db.rows[TRACK], None)  # no video known: offset
        self.assertEqual(row["youtubeTime"], 300)

    def test_a_failing_mapper_falls_back_to_the_offset(self):
        class Broken:
            def to_podcast(self, *a):
                raise RuntimeError("boom")

            to_youtube = to_podcast

        self.svc.position_mappers.insert(0, Broken())
        self.anchor()
        with self.assertLogs("podsync", "ERROR"):
            self.assertEqual(self.svc.resume(VID, 0)["sync"], "transcript")  # the next mapper still answers
        self.svc.position_mappers[:] = [Broken()]
        with self.assertLogs("podsync", "ERROR"):
            self.assertEqual(self.svc.resume(VID, 0)["sync"], "offset")

    # ---- -15 s / +15 s ---------------------------------------------------------------
    def test_nudge_with_a_map_is_kept_for_the_episode(self):
        self.anchor()
        before = self.svc.resume(VID, 0)["time"]
        r = self.call("nudge", {"videoId": VID, "delta": 15})
        self.assertEqual((r["saved"], r["scope"], r["correction"]), (True, "episode", 15))
        self.assertEqual(self.svc._offset(COLLECTION), 0, "the show offset is untouched")
        self.assertAlmostEqual(self.svc.resume(VID, 0)["time"], before + 15, delta=0.01)
        p = self.svc.progress(VID, JUMP_YT + 115, "pause")
        self.assertAlmostEqual(p["podcastTime"], JUMP_YT + 123.6, delta=0.6)

    def test_nudge_without_a_map_is_kept_for_the_show(self):
        self.svc.match(VID)
        r = self.call("nudge", {"videoId": VID, "delta": 15})
        self.assertEqual((r["scope"], r["learned"], r["offset"]), ("show", 15, 15))

    def test_episode_correction_is_clamped_and_validated(self):
        self.anchor()
        for _ in range(45):
            r = self.call("nudge", {"videoId": VID, "delta": 15})
        self.assertEqual((r["correction"], r["clamped"]), (600, True))
        for body in ({"videoId": VID, "delta": 0}, {"videoId": VID, "delta": 61}, {"videoId": "bad", "delta": 15}):
            with self.assertRaises(BadRequest):
                self.call("nudge", body)

    # ---- dynamic ads ------------------------------------------------------------------
    def test_transcript_of_another_length_is_approximate(self):
        r = self.anchor(dur=3729.0 - 60)  # your file has 60 s more ads than Apple's transcript
        self.assertEqual((r["anchored"], r["approx"]), (True, True))
        self.assertTrue(self.call("transcript", {"videoId": VID})["approx"])
        self.assertEqual(self.call("nudge", {"videoId": VID, "delta": -15})["scope"], "episode")

    # ---- what is kept ------------------------------------------------------------------
    def test_only_numbers_are_saved_and_they_survive_a_restart(self):
        self.anchor()
        self.call("nudge", {"videoId": VID, "delta": 15})
        saved = (self.cfg.state_dir / "anchors.json").read_text()
        for _, w in self.video_words[:50] + self.audio_words[:50]:
            self.assertNotIn(f'"{w}"', saved)
        self.assertLess(len(saved), 4000)
        self.assertNotIn("anchors", json.dumps(State(self.cfg.state_dir).data))
        svc = self._service()  # a new helper process
        self.assertAlmostEqual(svc.resume(VID, 0)["time"], 300 - 23.6 + 15, delta=0.6)

    def test_old_maps_are_dropped(self):
        self.write_ttml()
        with mock.patch.object(ta, "MAX_MAPS", 2):
            store = self.svc.transcripts.store
            for i, vid in enumerate(("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc")):
                store.clock = lambda i=i: 1000.0 + i
                store.put(vid, TRACK, {"failed": "x"})
            self.assertIsNone(store.get("aaaaaaaaaaa", TRACK))
            self.assertIsNotNone(store.get("ccccccccccc", TRACK))

    def test_damaged_store_is_ignored(self):
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.state_dir / "anchors.json").write_text('{"maps": {"abcdefghijk:%d": {"anchors": [[1, 0, 0], [2, -5, 0]]}}}' % TRACK)
        svc = self._service()
        svc.match(VID)
        with self.assertLogs("podsync.transcript", "WARNING"):
            self.assertEqual(svc.resume(VID, 0)["sync"], "offset")

    # ---- the transcript folder ------------------------------------------------------------
    def test_missing_folder(self):
        self.svc.match(VID)
        self.assertEqual(self.svc.status()["transcripts"]["folder"], "not_checked")
        self.assertEqual(self.call("transcript", {"videoId": VID})["reason"], "no_transcript")
        self.assertEqual(self.svc.status()["transcripts"], {"folder": "not_found", "maps": 0})

    def test_folder_not_allowed_is_quiet(self):
        self.ttml_dir.mkdir(parents=True)
        self.svc.match(VID)

        def denied(top, onerror=None):
            onerror(PermissionError(1, "Operation not permitted", str(top)))
            return iter(())

        with mock.patch.object(ta.os, "walk", denied), self.assertLogs("podsync.transcript", "WARNING") as logs:
            for _ in range(3):
                self.svc.transcripts.files._at = float("-inf")
                self.assertEqual(self.call("transcript", {"videoId": VID})["reason"], "no_transcript")
        self.assertEqual(len(logs.records), 1, "logged once")
        self.assertEqual(self.svc.status()["transcripts"]["folder"], "not_allowed")

    def test_huge_file_is_skipped(self):
        self.write_ttml()
        self.svc.match(VID)
        with mock.patch.object(ta, "MAX_TTML_BYTES", 100):
            r = self.call("captions", {"videoId": VID, "words": [list(w) for w in self.video_words]})
        self.assertEqual(r["reason"], "no_transcript")

    def test_fake_library_never_reads_real_transcripts(self):
        with mock.patch.dict(os.environ, {"PODSYNC_FAKE_LIBRARY": "/dev/null"}):
            os.environ.pop("PODSYNC_TRANSCRIPTS_DIR", None)
            svc = self._service()
        self.assertIsNone(svc.transcripts.files.root)
        self.assertEqual(svc.status()["transcripts"]["folder"], "off")


if __name__ == "__main__":
    unittest.main()
