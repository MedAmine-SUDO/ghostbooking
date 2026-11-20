"""The Keep / Release endpoint: GET /r/<token> shows a confirm page, POST applies it.

Opening a link never changes anything. Slack and Teams previews and email link scanners open
links on their own, so only the button on the page (a POST) keeps or releases the room.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import audit
from .engine import Engine
from .links import BadLink, read_token

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{title} · Ghostbooking</title>
<style>
  :root {{ color-scheme: light dark; --bg: #f6f7f9; --card: #fff; --text: #1d2330; --muted: #5d6675;
           --accent: #2456c8; --danger: #b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #14171c; --card: #1e232b; --text: #e8ebf0;
           --muted: #9aa3b2; --accent: #7aa2ff; --danger: #ff8a80; }} }}
  body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: var(--bg);
          color: var(--text); font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ background: var(--card); max-width: 26rem; margin: 1rem; padding: 2rem; border-radius: 14px;
          box-shadow: 0 2px 12px rgb(0 0 0 / .08); }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
  p {{ margin: 0 0 1.25rem; color: var(--muted); }}
  button {{ font: inherit; font-weight: 600; border: 0; border-radius: 10px; padding: .75rem 1.25rem;
            width: 100%; cursor: pointer; color: #fff; background: var(--accent); }}
  button.release {{ background: var(--danger); }}
  small {{ display: block; margin-top: 1.25rem; color: var(--muted); }}
</style></head>
<body><main><h1>{title}</h1><p>{text}</p>{form}<small>👻 Ghostbooking</small></main></body></html>
"""


def page(title: str, text: str, form: str = "") -> bytes:
    return PAGE.format(title=escape(title), text=escape(text), form=form).encode()


def make_server(engine: Engine, now: Callable[[], datetime]) -> ThreadingHTTPServer:
    assert engine.cfg.links is not None
    secret = engine.cfg.links.secret

    class Handler(BaseHTTPRequestHandler):
        server_version = "Ghostbooking"

        def _send(self, status: int, body: bytes, kind: str = "text/html; charset=utf-8") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _token(self):
            if not self.path.startswith("/r/"):
                self._send(404, page("Not found", "There's nothing here."))
                return None
            try:
                return read_token(secret, self.path[3:].split("?")[0], now())
            except BadLink as e:
                self._send(400, page("Link not valid", str(e)))
                return None

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, b"ok", "text/plain")
                return
            data = self._token()
            if data is None:
                return
            what = f"{data.room}, {data.day:%a %d %b} {data.start:%H:%M}–{data.end:%H:%M}"
            if data.action == "keep":
                title, text, label, cls = "Keep this room?", f"{what} stays booked for you.", "Keep it", ""
            else:
                title, text = "Release this room?", f"{what} will be free for others to book."
                label, cls = "Release the room", ' class="release"'
            form = f'<form method="post"><button{cls}>{escape(label)}</button></form>'
            self._send(200, page(title, text, form))

        def do_POST(self) -> None:
            data = self._token()
            if data is None:
                return
            try:
                title, text = engine.answer(data, now())
            except Exception as e:  # noqa: BLE001 - a click must never take the server down
                audit.record(engine.cfg.log, "link_failed", action=data.action, error=str(e))
                self._send(500, page("Something went wrong", "Please try again in a minute."))
                return
            self._send(200, page(title, text))

        def log_message(self, fmt: str, *args) -> None:
            pass  # tokens are in the URL; keep them out of the logs

    host, port = engine.cfg.links.address()
    return ThreadingHTTPServer((host, port), Handler)


def start(engine: Engine, now: Callable[[], datetime]) -> ThreadingHTTPServer:
    server = make_server(engine, now)
    threading.Thread(target=server.serve_forever, name="ghostbooking-web", daemon=True).start()
    return server
