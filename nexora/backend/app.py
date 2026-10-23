"""Transport-independent request router, shared by the local server and AWS Lambda."""
from __future__ import annotations

import json
from pathlib import Path

from . import engine
from .validation import MAX_BYTES, validate_csv

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = MAX_BYTES * 2 + 100_000  # JSON-escaped CSV can be a bit larger than the CSV itself

STATIC = {
    "/": ("frontend/index.html", "text/html; charset=utf-8"),
    "/index.html": ("frontend/index.html", "text/html; charset=utf-8"),
    "/style.css": ("frontend/style.css", "text/css; charset=utf-8"),
    "/app.js": ("frontend/app.js", "application/javascript; charset=utf-8"),
    "/favicon.svg": ("frontend/favicon.svg", "image/svg+xml"),
}
SAMPLE_FILE = ROOT / "data" / "sample_transactions.csv"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
}


def _json(status: int, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return status, {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", **SECURITY_HEADERS}, body


def _file(path: Path, ctype: str, extra=None):
    try:
        data = path.read_bytes()
    except OSError:
        return _json(404, {"error": "Not found"})
    return 200, {"Content-Type": ctype, "Cache-Control": "no-cache", **SECURITY_HEADERS, **(extra or {})}, data


def handle_request(method: str, path: str, body: bytes = b""):
    """Return (status, headers, body_bytes). Never raises."""
    try:
        path = path.split("?", 1)[0]
        method = method.upper()
        if method == "GET":
            if path in STATIC:
                rel, ctype = STATIC[path]
                return _file(ROOT / rel, ctype)
            if path == "/api/health":
                return _json(200, {"status": "ok", "version": engine.VERSION})
            if path in ("/api/sample", "/sample_transactions.csv"):
                extra = {"Content-Disposition": 'attachment; filename="sample_transactions.csv"'} if path.endswith(".csv") else None
                return _file(SAMPLE_FILE, "text/csv; charset=utf-8", extra)
            if path in ("/api/validate", "/api/analyze"):
                return _json(405, {"error": "Method not allowed"})
            return _json(404, {"error": "Not found"})
        if method == "POST" and path in ("/api/validate", "/api/analyze"):
            if len(body) > MAX_BODY:
                return _json(413, {"error": "Request too large (limit about 1 MB of CSV)."})
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return _json(400, {"error": "Request body must be valid JSON."})
            if not isinstance(data, dict) or not isinstance(data.get("csv"), str):
                return _json(400, {"error": "Send a JSON object with a 'csv' text field."})
            if path == "/api/validate":
                return _json(200, validate_csv(data["csv"]).to_public())
            try:
                result = engine.analyze(data["csv"], data.get("settings"))
            except ValueError as exc:
                return _json(400, {"error": f"Invalid settings: {exc}"})
            return _json(200 if result["ok"] else 422, result)
        if path in STATIC or path.startswith("/api/"):
            return _json(405, {"error": "Method not allowed"})
        return _json(404, {"error": "Not found"})
    except Exception:  # noqa: BLE001 - never leak internals to the client
        return _json(500, {"error": "Unexpected server error. Check that your CSV follows the documented schema."})
