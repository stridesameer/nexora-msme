"""Local development server: python backend/server.py [--port 8000]

Binds to 127.0.0.1 only. Nothing is written to disk and no data is stored.
"""
from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.app import MAX_BODY, handle_request  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    server_version = "NexoraLocal/0.1"

    def _send(self, status, headers, body):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        self._send(*handle_request("GET", self.path))

    def do_POST(self):  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY:
            self._send(*handle_request("POST", self.path, b"x" * (MAX_BODY + 1)))
            return
        self._send(*handle_request("POST", self.path, self.rfile.read(length)))

    def log_message(self, fmt, *args):  # keep the console quiet and never log bodies
        sys.stderr.write("%s %s\n" % (self.command, self.path))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"NEXORA running at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
