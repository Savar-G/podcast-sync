"""The -15 s / +15 s buttons teach the helper a show's offset."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))
sys.path.insert(0, str(ROOT / "tests"))

import test_service  # noqa: E402
from podsync.service import BadRequest  # noqa: E402
from podsync.state import State  # noqa: E402
from test_service import COLLECTION  # noqa: E402

VID = "abcdefghijk"


class NudgeTest(unittest.TestCase):
    setUp = test_service.ServiceTest.setUp
    _service = test_service.ServiceTest._service

    def nudge(self, delta, video_id=VID):
        return self.svc.handlers["nudge"]({"videoId": video_id, "delta": delta})

    def test_each_press_adds_to_the_show_offset(self):
        self.svc.match(VID)
        self.assertEqual(self.nudge(15)["learned"], 15)
        self.assertEqual(self.nudge(15)["learned"], 30)
        r = self.nudge(-15)
        self.assertEqual((r["saved"], r["learned"], r["offset"], r["show"]), (True, 15, 15, "David Senra"))

    def test_learned_offset_moves_the_youtube_jump(self):
        self.svc.match(VID)
        self.nudge(15)
        r = self.svc.resume(VID, 0)
        self.assertEqual((r["action"], r["time"], r["label"], r["markerTime"]), ("seek", 2445, "40:45", 2445))

    def test_learned_offset_moves_the_iphone_link(self):
        self.svc.match(VID)
        self.nudge(-15)
        p = self.svc.progress(VID, 1000, "pause")
        self.assertEqual(p["podcastTime"], 1015)
        self.assertTrue(p["url"].endswith("&t=1015"))
        info = json.loads((self.cfg.handoff_dir / "resume.json").read_text())
        self.assertEqual(info["seconds"], 1015)

    def test_learned_offset_adds_to_the_configured_one(self):
        for s in self.cfg.shows:
            if COLLECTION in s.apple_ids:
                s.offset_seconds = 90
        self.svc.match(VID)
        r = self.nudge(-15)
        self.assertEqual((r["learned"], r["offset"]), (-15, 75))
        self.assertEqual(self.svc.resume(VID, 0)["time"], 2505)

    def test_learned_offset_is_clamped(self):
        self.svc.match(VID)
        for _ in range(45):
            r = self.nudge(15)
        self.assertEqual((r["learned"], r["clamped"]), (600, True))
        self.assertEqual(self.nudge(-15)["learned"], 585)

    def test_it_is_kept_per_show_and_survives_a_restart(self):
        self.svc.match(VID)
        self.nudge(15)
        svc = self._service()  # a new helper process reads state.json again
        self.assertEqual(svc._offset(COLLECTION), 15)
        self.assertEqual(svc._offset(999), 0)
        self.assertEqual(State(self.cfg.state_dir).data["offsets"], {str(COLLECTION): 15})

    def test_unmatched_video_is_not_saved(self):
        r = self.nudge(15, video_id="zzzzzzzzzzz")
        self.assertEqual((r["saved"], r["reason"]), (False, "not_matched"))

    def test_bad_input_is_refused(self):
        self.svc.match(VID)
        for body in (
            {"videoId": VID},
            {"videoId": VID, "delta": "x"},
            {"videoId": VID, "delta": 0},
            {"videoId": VID, "delta": 61},
            {"videoId": VID, "delta": float("nan")},
            {"videoId": VID, "delta": float("inf")},
            {"videoId": "../../etc/pw", "delta": 15},
            {"delta": 15},
        ):
            with self.assertRaises(BadRequest, msg=body):
                self.svc.handlers["nudge"](body)
        self.assertEqual(self.svc._offset(COLLECTION), 0)


if __name__ == "__main__":
    unittest.main()
