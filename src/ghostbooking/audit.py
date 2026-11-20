"""Append-only log of every change, so a team can always see what was removed and why."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import Booking


def read(log: Path, since: datetime, until: datetime) -> list[dict]:
    """Entries logged in [since, until). Unreadable lines are skipped."""
    out = []
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return out
    lo, hi = since.isoformat(), until.isoformat()
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if lo <= entry.get("at", "") < hi:
            out.append(entry)
    return out


def booking_dict(b: Booking) -> dict:
    return {"row": b.row, "room": b.room, "when": b.when(), "booked_by": b.booked_by, "title": b.title,
            "day": b.day, "start": f"{b.start:%H:%M}", "end": f"{b.end:%H:%M}"}


def record(log: Path, event: str, at: datetime | None = None, **data) -> None:
    """`at` is the team's local time (the engine's clock); defaults to now."""
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": (at or datetime.now()).isoformat(timespec="seconds"), "event": event, **data}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
