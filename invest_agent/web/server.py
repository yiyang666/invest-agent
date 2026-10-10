"""Loopback-only dashboard with one reviewed market-data refresh action."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import time
import threading
from urllib.parse import urlsplit, parse_qs

from invest_agent.credentials import (
    CredentialStoreError,
    INTEGRATION_SPECS,
    KeychainCredentialStore,
    integration_status,
)
from .read_model import Dashboard

STATIC = Path(__file__).parent / "static"


def make_handler(root: Path, credential_store=None):
    root = root.resolve()
    model = Dashboard(root)
    cached = {"at": 0, "data": None}
    lock = threading.Lock()
    refresh_lock = threading.Lock()
    credential_lock = threading.Lock()
    credential_store = credential_store or KeychainCredentialStore()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def allowed_request(self):
            # A foreign origin cannot use this private read service via DNS rebinding.
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"localhost", "127.0.0.1"}:
                self.send_error(403)
                return False
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                self.send_error(403)
                return False
            return True

        def do_GET(self):
            if not self.allowed_request():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/dashboard":
                    with lock:
                        if time.monotonic() - cached["at"] > 15 or cached["data"] is None:
                            cached.update(at=time.monotonic(), data=model.payload())
                        self.reply(json.dumps(cached["data"], ensure_ascii=False).encode(), "application/json")
                elif url.path == "/api/integrations":
                    with credential_lock:
                        payload = {"items": integration_status(credential_store)}
                    self.reply(json.dumps(payload, ensure_ascii=False).encode(), "application/json")
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

        def do_POST(self):
            if not self.allowed_request():
                return
            url = urlsplit(self.path)
            if url.path == "/api/market-refresh":
                self.refresh_market()
            elif url.path == "/api/integrations":
                self.save_integration()
            else:
                self.send_error(404)

        def content_length(self, maximum: int) -> int | None:
            try:
                value = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                self.send_error(400)
                return None
            if value < 0 or value > maximum:
                self.send_error(413)
                return None
            return value

        def save_integration(self):
            content_length = self.content_length(8192)
            if content_length is None:
                return
            if not content_length or self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
                self.send_error(400)
                return
            try:
                payload = json.loads(self.rfile.read(content_length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_error(400)
                return
            provider = payload.get("provider") if isinstance(payload, dict) else None
            token = payload.get("token") if isinstance(payload, dict) else None
            if provider not in INTEGRATION_SPECS or not isinstance(token, str):
                self.send_error(400)
                return
            token = token.strip()
            if not 8 <= len(token) <= 4096 or any(ord(character) < 33 or ord(character) == 127 for character in token):
                self.reply(b'{"error":"invalid_token"}', "application/json", 400)
                return
            try:
                with credential_lock:
                    credential_store.set(provider, token)
                self.reply(
                    json.dumps({"status": "saved", "provider": provider}).encode(),
                    "application/json",
                )
            except CredentialStoreError as error:
                self.reply(json.dumps({"error": error.code}).encode(), "application/json", 503)
            finally:
                token = None

        def refresh_market(self):
            content_length = self.content_length(0)
            if content_length is None:
                return
            if content_length != 0:
                self.send_error(400)
                return
            if not os.environ.get("JIN10_MCP_TOKEN"):
                try:
                    with credential_lock:
                        if not credential_store.configured("jin10"):
                            self.reply(b'{"error":"credential_unavailable"}', "application/json", 503)
                            return
                except CredentialStoreError:
                    self.reply(b'{"error":"credential_unavailable"}', "application/json", 503)
                    return
            if not refresh_lock.acquire(blocking=False):
                self.reply(b'{"error":"refresh_in_progress"}', "application/json", 409)
                return
            try:
                result = subprocess.run(
                    [str(root / "scripts/refresh_market_quotes.sh")],
                    cwd=root, capture_output=True, text=True, timeout=180, check=False,
                )
                if result.returncode == 78:
                    self.reply(b'{"error":"credential_unavailable"}', "application/json", 503)
                    return
                if result.returncode != 0:
                    self.reply(b'{"error":"Market refresh failed. Existing local quotes remain available."}', "application/json", 503)
                    return
                with lock:
                    cached.update(at=0, data=None)
                self.reply(b'{"status":"complete"}', "application/json")
            except (OSError, subprocess.TimeoutExpired):
                self.reply(b'{"error":"Market refresh could not complete. Existing local quotes remain available."}', "application/json", 503)
            finally:
                refresh_lock.release()

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
