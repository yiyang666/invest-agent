"""Loopback-only HTTP server for the local dashboard; no remote data access."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import time
import threading
from urllib.parse import urlsplit, parse_qs

from .read_model import Dashboard

STATIC = Path(__file__).parent / "static"


def make_handler(root: Path):
    model = Dashboard(root)
    cached = {"at": 0, "data": None}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            # A foreign origin cannot use this private read service via DNS rebinding.
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"localhost", "127.0.0.1"}:
                self.send_error(403)
                return
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send_error(403)
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/dashboard":
                    with lock:
                        if time.monotonic() - cached["at"] > 15 or cached["data"] is None:
                            cached.update(at=time.monotonic(), data=model.payload())
                        self.reply(json.dumps(cached["data"], ensure_ascii=False).encode(), "application/json")
                elif url.path == "/api/nav":
                    code = parse_qs(url.query).get("code", [""])[0]
                    if not re.fullmatch(r"\d{6}", code):
                        self.send_error(400)
                        return
                    self.reply(json.dumps(model.series(code)).encode(), "application/json")
                elif url.path in {"/", "/app.js", "/style.css"}:
                    name = "index.html" if url.path == "/" else url.path[1:]
                    kind = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                    self.reply((STATIC / name).read_bytes(), kind)
                else:
                    self.send_error(404)
            except Exception:
                self.reply(b'{"error":"Local data could not be loaded. Check the data store and configuration."}', "application/json", 503)

        def reply(self, body: bytes, kind: str, status: int = 200):
            self.send_response(status)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Open the private local investment dashboard")
    parser.add_argument("--workspace-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.workspace_root))
    print(f"Invest Agent: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
