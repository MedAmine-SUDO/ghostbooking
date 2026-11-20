"""What gets sent. Channels only need to send text with links, so adding one stays small."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Link:
    label: str
    url: str


@dataclass(frozen=True)
class Message:
    key: str  # stable id: a key that was sent once is never sent again
    kind: str  # "conflict", "summary", "skipped", ...
    text: str
    to: str | None = None  # "Booked by" as written in the sheet; None = the team channel
    subject: str = "Ghostbooking"
    links: tuple[Link, ...] = ()
    covers: tuple[str, ...] = ()  # more keys to mark as sent with this one
