"""Append-only log of every change, so a team can always see what was removed and why."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .models import Booking


def booking_dict(b: Booking) -> dict:
    return {"row": b.row, "room": b.room, "when": b.when(), "booked_by": b.booked_by, "title": b.title}


def record(log: Path, event: str, **data) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now().isoformat(timespec="seconds"), "event": event, **data}
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
