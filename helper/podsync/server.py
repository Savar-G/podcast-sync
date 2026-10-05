"""Local HTTP API for the extension. Listens on 127.0.0.1 only.

Who may call it:
  * the Podcast Sync extension (Origin chrome-extension://<pinned id>), or
  * a local tool with no Origin header (curl, tests) that sets X-Podsync: 1.
Web pages are refused: they always send an Origin, and a DNS-rebinding page
also fails the Host check.
"""
from __future__ import annotations

import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, Optional

from .service import SyncService

log = logging.getLogger("podsync.http")

MAX_BODY = 16 * 1024


def make_handler(service: SyncService, extension_ids: Iterable[str], port: int):
    allowed_origins = {f"chrome-extension://{i}" for i in extension_ids}
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "podsync/0.2"

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

        def _send(self, code: int, body: Dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _trusted(self) -> bool:
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin: Optional[str] = self.headers.get("Origin")
            if origin is not None:
                return origin in allowed_origins
            return self.headers.get("X-Podsync") == "1"

        def do_OPTIONS(self):  # never approve a CORS preflight
            self._send(403, {"error": "forbidden"})

        def do_GET(self):
            if self.path == "/health":
                return self._send(200, {"ok": True})
            if not self._trusted():
                return self._send(403, {"error": "forbidden"})
            if self.path == "/status":
                return self._send(200, service.status())
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._trusted():
                return self._send(403, {"error": "forbidden"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self._send(413, {"error": "too large"})
                body = json.loads(self.rfile.read(length) or b"{}")
                video_id = str(body["videoId"])
                current = float(body.get("currentTime") or 0)
                if not re.fullmatch(r"[\w-]{11}", video_id) or not 0 <= current < 86400:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                return self._send(400, {"error": "bad request"})
            hint = {"title": body.get("title"), "duration": body.get("duration")}
            try:
                if self.path == "/match":
                    return self._send(200, service.match(video_id, hint))
                if self.path == "/resume":
                    return self._send(200, service.resume(video_id, current, hint))
                if self.path == "/progress":
                    return self._send(200, service.progress(video_id, current, str(body.get("event") or ""), hint))
            except Exception:
                log.exception("request failed")
                return self._send(500, {"error": "internal error"})
            self._send(404, {"error": "not found"})

    return Handler


def serve(service: SyncService, port: int, extension_ids: Iterable[str] = (), host: str = "127.0.0.1") -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), None)  # bind first so port=0 resolves
    httpd.RequestHandlerClass = make_handler(service, extension_ids, httpd.server_address[1])
    httpd.daemon_threads = True
    return httpd
