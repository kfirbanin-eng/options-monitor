"""Local web page server (stdlib only, bound to 127.0.0.1).

GET  /            -> static/index.html (chart + panels, polls /api/state every 5 s)
GET  /api/state   -> the monitor's latest snapshot as JSON
POST /api/exit    -> {"price": optional, "reason": "..."} journals your exit.
                     This only writes a CSV row locally; nothing goes to the broker.
                     Requires header X-Monitor: 1 so other websites can't trigger it.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"


def start_web(monitor, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, json.dumps(monitor.get_snapshot(), default=str).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/exit" or self.headers.get("X-Monitor") != "1":
                return self._send(403, b"forbidden", "text/plain")
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
                price = body.get("price")
                price = float(price) if price not in (None, "") else None
            except (ValueError, TypeError):
                return self._send(400, b'{"ok":false,"error":"bad price"}', "application/json")
            monitor.request_exit(price, str(body.get("reason", "manual exit"))[:200])
            self._send(200, b'{"ok":true}', "application/json")

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
