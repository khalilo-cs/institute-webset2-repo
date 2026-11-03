"""HTTP API + web UI (stdlib only). Works from a phone browser, and is the
same API an Android shell app talks to."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .chat import ChatEngine
from .core import Mosaic

STATIC = Path(__file__).parent / "static"
MAX_BODY = 256 * 1024


def make_handler(mo: Mosaic, chat: ChatEngine | None = None):
    chat = chat or ChatEngine(mo)

    class Handler(BaseHTTPRequestHandler):
        server_version = "MOSAIC/0.1"

        def log_message(self, fmt, *args):  # quiet
            pass

        def _send(self, code: int, payload, ctype: str = "application/json; charset=utf-8"):
            body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False,
                                                                        default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                raise ValueError("Content-Type must be application/json")
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError("JSON object expected")
            return data

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/app.js":
                return self._send(200, (STATIC / "app.js").read_bytes(), "text/javascript; charset=utf-8")
            if path == "/api/dashboard":
                d = mo.dashboard()
                d["chat"] = {**chat.stats(), **chat.status()}
                return self._send(200, d)
            if path == "/api/chat/conversations":
                return self._send(200, chat.conversations())
            if path == "/api/chat/messages":
                from urllib.parse import parse_qs, urlparse
                cid = (parse_qs(urlparse(self.path).query).get("id") or [""])[0]
                try:
                    return self._send(200, chat.messages(cid))
                except KeyError as e:
                    return self._send(404, {"error": str(e)})
            if path == "/api/chat/status":
                return self._send(200, chat.status())
            if path == "/api/memory":
                return self._send(200, chat.preferences())
            if path == "/api/settings":
                from .memory import DEFAULT_SETTINGS
                return self._send(200, {k: mo.m.setting(k) for k in DEFAULT_SETTINGS})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                b = self._body()
                if path == "/api/chat/send":
                    return self._send(200, chat.send(b.get("conversation_id") or None, str(b.get("text", ""))))
                if path == "/api/chat/rate":
                    return self._send(200, chat.rate(str(b["message_id"]), bool(b.get("good", False)),
                                                     b.get("correction") or None))
                if path == "/api/chat/delete":
                    chat.delete_conversation(str(b["conversation_id"]))
                    return self._send(200, {"ok": True})
                if path == "/api/memory/delete":
                    chat.delete_preference(str(b["id"]))
                    return self._send(200, {"ok": True})
                if path == "/api/task":
                    return self._send(200, mo.solve(str(b["category"]), str(b["input"]),
                                                    private=bool(b.get("private", False))))
                if path == "/api/feedback":
                    return self._send(200, mo.feedback(str(b["episode_id"]), str(b["kind"]),
                                                       b.get("correct_answer"), str(b.get("note", ""))))
                if path == "/api/learn":
                    return self._send(200, mo.learn_cycle())
                if path == "/api/approve":
                    mo.decide(str(b["improvement_id"]), bool(b["accept"]))
                    return self._send(200, {"ok": True})
                if path == "/api/rollback":
                    return self._send(200, {"active": mo.rollback(str(b["category"]))})
                if path == "/api/category":
                    mo.add_category(str(b["name"]), str(b["kind"]), str(b["description"]),
                                    str(b.get("language", "ar")))
                    return self._send(200, {"ok": True})
                if path == "/api/examples":
                    pairs = [(str(p[0]), str(p[1])) for p in b["pairs"]]
                    split = b.get("split") if b.get("split") in ("dev", "heldout") else None
                    return self._send(200, mo.add_examples(str(b["category"]), pairs, "human", True, split))
                if path == "/api/settings":
                    for k, v in b.items():
                        mo.m.set_setting(k, v)
                    return self._send(200, {"ok": True})
                if path == "/api/device":
                    mo.scheduler.update_device(**b)
                    return self._send(200, mo.scheduler.device)
                if path == "/api/category/unfreeze":
                    mo.unfreeze(str(b["category"]))
                    return self._send(200, {"ok": True})
                if path == "/api/category/ungraduate":
                    mo.ungraduate(str(b["category"]))
                    return self._send(200, {"ok": True})
                if path == "/api/compact":
                    return self._send(200, mo.m.compact())
                self._send(404, {"error": "not found"})
            except (KeyError, ValueError, TypeError, PermissionError, json.JSONDecodeError) as e:
                self._send(400, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def serve(mo: Mosaic, host: str = "127.0.0.1", port: int = 8765, chat: ChatEngine | None = None) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(mo, chat))
    print(f"MOSAIC يعمل على http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    serve(Mosaic(os.environ.get("MOSAIC_DB", "mosaic.db")))
