"""Signed Keep / Release links. They work in any channel that can show a URL.

A token carries the booking it is about and expires when the meeting ends. It is signed with
HMAC-SHA256, so nobody can make one for someone else's booking without the secret.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import date, datetime, time

from .config import Links
from .models import Booking
from .notify import Link


class BadLink(ValueError):
    pass


@dataclass(frozen=True)
class LinkData:
    action: str  # keep | release
    room: str
    day: date
    start: time
    end: time
    booked_by: str
    title: str = ""

    @property
    def occurrence(self) -> str:
        return self.booking().occurrence(self.day)

    @property
    def expires(self) -> datetime:
        return datetime.combine(self.day, self.end)

    def booking(self) -> Booking:
        return Booking(row=0, room=self.room, start=self.start, end=self.end,
                       booked_by=self.booked_by, title=self.title, day=self.day)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sig(secret: str, payload: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()[:16])


def make_token(secret: str, data: LinkData) -> str:
    payload = _b64(json.dumps(
        [data.action, data.room, data.day.isoformat(), f"{data.start:%H:%M}", f"{data.end:%H:%M}",
         data.booked_by, data.title], ensure_ascii=False, separators=(",", ":")).encode())
    return f"{payload}.{_sig(secret, payload)}"


def read_token(secret: str, token: str, now: datetime) -> LinkData:
    payload, _, sig = token.partition(".")
    if not sig or not hmac.compare_digest(sig, _sig(secret, payload)):
        raise BadLink("This link isn't valid. It may have been cut short when it was copied.")
    try:
        action, room, day, start, end, booked_by, title = json.loads(_unb64(payload))
        data = LinkData(action, room, date.fromisoformat(day), time.fromisoformat(start),
                        time.fromisoformat(end), booked_by, title)
    except (ValueError, TypeError) as e:
        raise BadLink("This link isn't valid.") from e
    if data.action not in ("keep", "release"):
        raise BadLink("This link isn't valid.")
    if now >= data.expires:
        raise BadLink(f"This link has expired: the meeting ended at {data.end:%H:%M}.")
    return data


def booking_links(cfg: Links, b: Booking, day: date) -> tuple[Link, ...]:
    def url(action: str) -> str:
        data = LinkData(action, b.room, day, b.start, b.end, b.booked_by, b.title)
        return f"{cfg.base_url.rstrip('/')}/r/{make_token(cfg.secret, data)}"

    return Link("Keep it", url("keep")), Link("Release the room", url("release"))
