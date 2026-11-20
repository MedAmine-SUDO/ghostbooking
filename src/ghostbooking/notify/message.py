"""What gets sent. Channels only need to send text with links, so adding one stays small."""

from __future__ import annotations

import re
from dataclasses import dataclass

_BOLD = re.compile(r"\*\*(.+?)\*\*")


def bold(text: str, start: str, end: str | None = None) -> str:
    """Turn the **bold** in message text into a channel's own markup ("" and "" for plain text)."""
    return _BOLD.sub(lambda m: f"{start}{m.group(1)}{start if end is None else end}", text)


def plain(text: str) -> str:
    return bold(text, "")


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
