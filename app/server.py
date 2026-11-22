#!/usr/bin/env python3
"""Local web server (standard library only). Run: python3 server.py [--port 8000]

Binds to 127.0.0.1. The Claude API key is read from ANTHROPIC_API_KEY or entered in the UI
(kept in memory only). Claim checks never call the AI.
"""

import argparse
import json
import mimetypes
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
from claimcheck.ai import AIUnavailable  # noqa: E402
from claimcheck.drafts import DraftError  # noqa: E402
from claimcheck.session import Session  # noqa: E402

WEB = Path(__file__).resolve().parent / "web"
MAX_BODY = 25 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    session: Session = None
    server_version = "ClaimCheck/1.0"

    def log_message(self, fmt, *args):  # request lines only; bodies (which may hold keys) are never logged
        sys.stderr.write("%s %s\n" % (self.command, urlparse(self.path).path))

    # plumbing ---------------------------------------------------------
    def send(self, code, body, ctype="application/json", headers=None):
        data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Upload too large")
        return self.rfile.read(n).decode("utf-8-sig") if n else ""

    def json_body(self):
        text = self.body()
        return json.loads(text) if text else {}

    def local_only(self):
        """Reject cross-site requests (DNS rebinding / CSRF) to the local app."""
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if host not in ("127.0.0.1", "localhost"):
            return False
        return not origin or urlparse(origin).hostname in ("127.0.0.1", "localhost")

    def handle_any(self, method):
        path = urlparse(self.path).path
        if not self.local_only():
            return self.send(403, {"error": "Forbidden"})
        try:
            if path.startswith("/api/"):
                return self.api(method, path)
            if method != "GET":
                return self.send(405, {"error": "Method not allowed"})
            return self.static(path)
        except (ValueError, DraftError, json.JSONDecodeError) as e:
            self.send(400, {"error": str(e)})
        except KeyError as e:
            self.send(404, {"error": str(e.args[0]) if e.args else "Not found"})
        except AIUnavailable as e:
            self.send(503, {"error": str(e)})
        except Exception:  # never leak internals
            import traceback
            traceback.print_exc()
            self.send(500, {"error": "Internal error"})

    do_GET = lambda self: self.handle_any("GET")      # noqa: E731
    do_POST = lambda self: self.handle_any("POST")    # noqa: E731
    do_PUT = lambda self: self.handle_any("PUT")      # noqa: E731
    do_DELETE = lambda self: self.handle_any("DELETE")  # noqa: E731

    def static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (WEB / name).resolve()
        if WEB.resolve() not in target.parents or not target.is_file():
            return self.send(404, {"error": "Not found"})
        self.send(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")

    # API --------------------------------------------------------------
    def api(self, method, path):
        s = self.session
        m = lambda pattern: re.fullmatch(pattern, path)  # noqa: E731
        if (method, path) == ("GET", "/api/status"):
            return self.send(200, s.status())
        if (method, path) == ("POST", "/api/ai/key"):
            s.ai.set_key(str(self.json_body().get("key", "")))
            return self.send(200, {"ai_available": s.ai.available()})  # never echo the key
        if (method, path) == ("POST", "/api/data/sample"):
            s.load_sample_data()
            return self.send(200, s.status())
        if method == "POST" and (g := m(r"/api/data/(authorizations|evv|history)")):
            return self.send(200, s.load_reference(g.group(1), self.body()))
        if (method, path) == ("POST", "/api/claims"):
            return self.send(200, s.upload_claims(self.body()))
        if (method, path) == ("POST", "/api/claims/sample"):
            return self.send(200, s.load_sample_claims())
        if (method, path) == ("GET", "/api/results"):
            return self.send(200, s.results())
        if (method, path) == ("GET", "/api/export.csv"):
            return self.send(200, s.export(), "text/csv; charset=utf-8",
                             {"Content-Disposition": 'attachment; filename="claim_check_results.csv"'})
        if method == "POST" and (g := m(r"/api/claims/line/(\d+)/edit")):
            return self.send(200, s.edit_claim(int(g.group(1)), self.json_body().get("fields", {})))
        if method == "POST" and (g := m(r"/api/claims/line/(\d+)/revert")):
            return self.send(200, s.revert_edits(int(g.group(1))))
        if method in ("POST", "DELETE") and (g := m(r"/api/claims/(C\d{6})/override")):
            b = self.json_body() if method == "POST" else {}
            return self.send(200, s.set_override(g.group(1), b.get("status"), b.get("denial_codes"), b.get("note")))
        if (method, path) == ("GET", "/api/accuracy"):
            return self.send(200, s.accuracy())
        if (method, path) == ("GET", "/api/rules"):
            return self.send(200, s.rules_view())
        if (method, path) == ("POST", "/api/rules/reset"):
            s.reset_rules_to_supplied()
            return self.send(200, s.rules_view())
        if (method, path) == ("POST", "/api/drafts"):
            return self.send(200, s.create_draft(self.json_body()))
        if (g := m(r"/api/drafts/([0-9a-f]{8})(?:/(chat|decide|activate|examples))?")):
            did, action = g.groups()
            if method == "GET" and not action:
                return self.send(200, s.draft_view(s.draft(did)))
            if method == "PUT" and not action:
                return self.send(200, s.edit_draft(did, self.json_body()))
            if method == "POST" and action == "chat":
                return self.send(200, s.chat_draft(did, str(self.json_body().get("message", "")).strip()))
            if method == "POST" and action == "decide":
                return self.send(200, s.decide(did, self.json_body()))
            if method == "POST" and action == "examples":
                return self.send(200, s.add_example(did, self.json_body()))
            if method == "POST" and action == "activate":
                return self.send(200, s.activate(did, str(self.json_body().get("regression_reason", ""))))
        return self.send(404, {"error": "Not found"})


def make_server(session, port=8000, host="127.0.0.1"):
    handler = type("BoundHandler", (Handler,), {"session": session})
    return ThreadingHTTPServer((host, port), handler)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    server = make_server(Session(), args.port)
    print(f"Claim checker running at http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
