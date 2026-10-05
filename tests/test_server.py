import json
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "helper"))

from podsync.server import serve  # noqa: E402


class StubService:
    def match(self, video_id, hint):
        return {"matched": False, "reason": "stub"}

    def resume(self, video_id, current, hint):
        return {"action": "none", "reason": "stub", "videoId": video_id}

    def progress(self, video_id, current, event, hint):
        return {"matched": False, "event": event}

    def status(self):
        return {"ok": True}


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(StubService(), 0, ["goodextension"])
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def post(self, path, body, headers, data=None, method=None):
        data = data if data is not None else json.dumps(body).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    VID = {"videoId": "w3-nMklTFjY"}

    def test_extension_request_is_accepted(self):
        code, body = self.post("/resume", self.VID, {"Origin": "chrome-extension://goodextension"})
        self.assertEqual((code, body["reason"]), (200, "stub"))

    def test_local_tool_with_header_is_accepted(self):
        code, _ = self.post("/match", self.VID, {"X-Podsync": "1"})
        self.assertEqual(code, 200)

    def test_web_pages_and_other_extensions_are_rejected(self):
        for origin in ("https://evil.example", "chrome-extension://otherextension", "null"):
            code, _ = self.post("/progress", self.VID, {"X-Podsync": "1", "Origin": origin})
            self.assertEqual(code, 403, origin)
        code, _ = self.post("/progress", self.VID, {"Content-Type": "text/plain"})
        self.assertEqual(code, 403)

    def test_dns_rebinding_host_is_rejected(self):
        code, _ = self.post("/match", self.VID, {"X-Podsync": "1", "Host": f"evil.example:{self.port}"})
        self.assertEqual(code, 403)

    def test_preflight_is_refused(self):
        code, _ = self.post("/match", None, {"Origin": "https://evil.example"}, data=b"", method="OPTIONS")
        self.assertEqual(code, 403)

    def test_bad_body(self):
        for body in ({"nope": 1}, {"videoId": "../../etc"}, {"videoId": "w3-nMklTFjY", "currentTime": -5}):
            code, _ = self.post("/resume", body, {"X-Podsync": "1"})
            self.assertEqual(code, 400, body)

    def test_large_body_is_refused(self):
        code, _ = self.post("/resume", None, {"X-Podsync": "1"}, data=b"x" * 20000)
        self.assertEqual(code, 413)

    def test_health(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as r:
            self.assertEqual(json.load(r), {"ok": True})


if __name__ == "__main__":
    unittest.main()
