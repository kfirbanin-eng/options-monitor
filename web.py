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

        def _host_ok(self) -> bool:
            """Block DNS-rebinding: a hostile site that points its domain at 127.0.0.1 still
            sends its own name in Host/Origin, so only accept our own localhost names."""
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host", "") not in allowed:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin.split("://", 1)[-1] in allowed

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self' 'unsafe-inline' https://unpkg.com; "
                             "style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; "
                             "frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
            super().end_headers()

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/api/state":
                self._send(200, json.dumps(monitor.get_snapshot(), default=str).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if (self.path != "/api/exit" or self.headers.get("X-Monitor") != "1" or not self._host_ok()
                    or not self.headers.get("Content-Type", "").startswith("application/json")):
                return self._send(403, b"forbidden", "text/plain")
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if not 0 <= length <= 4096:
                    return self._send(413, b"too large", "text/plain")
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("body must be an object")
                price = body.get("price")
                price = float(price) if price not in (None, "") else None
                if price is not None and not (0 <= price < 1e6):
                    raise ValueError("price out of range")
            except (ValueError, TypeError):
                return self._send(400, b'{"ok":false,"error":"bad price"}', "application/json")
            monitor.request_exit(price, str(body.get("reason", "manual exit"))[:200])
            self._send(200, b'{"ok":true}', "application/json")

        def version_string(self):
            return "monitor"

        def log_message(self, *a):
            pass

    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("The monitor page must only listen on localhost")
    srv = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
