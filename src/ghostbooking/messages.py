"""Turn a reset plan into messages. Wording lives here, channels only deliver it."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from .config import Config
from .links import booking_links
from .models import Booking
from .notify import Message
from .rules import Conflict, Plan, is_protected

MAX_LINES = 10  # longer lists in the team summary end with "and N more"


def _span(b: Booking) -> str:
    return f"{b.start:%H:%M}–{b.end:%H:%M}"


def _title(b: Booking) -> str:
    return f" (“{b.title}”)" if b.title else ""


def conflict_message(c: Conflict, cfg: Config, removed: bool) -> Message:
    b, kept = c.clashing, c.kept
    owner = f"{kept.booked_by}'s" if kept.booked_by else "another"
    why = "is a protected meeting" if is_protected(kept, cfg) else "was booked first"
    where = f"{b.room} on {c.day:%a %d %b} at {_span(b)}{_title(b)}"
    other = f"{owner} booking, {_span(kept)}, which {why}."
    text = (f"Your booking of {where} was removed: it overlapped {other}" if removed
            else f"Heads up: your booking of {where} overlaps {other}")
    text += f" {c.suggestion}." if c.suggestion else " No other room is free at that time."
    return Message(key=f"conflict:{b.occurrence(c.day)}", kind="conflict", text=text, to=b.booked_by or None,
                   subject=f"Double booking: {b.room}, {c.day:%a %d %b} {_span(b)}")


def _bullets(lines: list[str]) -> list[str]:
    shown = [f"• {line}" for line in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        shown.append(f"• and {len(lines) - MAX_LINES} more")
    return shown


def summary_message(plan: Plan, source_name: str) -> Message | None:
    if not (plan.removals or plan.conflicts or plan.issues):
        return None
    n = len(plan.removals)
    lines = [f"Morning reset of {source_name}, {plan.now:%a %d %b}:"]
    if n:
        lines.append(f"Cleared {n} ghost booking{'s' * (n != 1)}:")
        lines += _bullets([f"{r.booking.room}, {r.booking.when()}, {r.booking.booked_by or '?'}"
                           for r in plan.removals])
    if plan.conflicts:
        n = len(plan.conflicts)
        lines.append(f"{n} double booking{'s' * (n != 1)}, the people involved have been told:")
        lines += _bullets([f"{c.clashing.room}, {c.day:%a %d %b} {_span(c.clashing)}: "
                           f"{c.clashing.booked_by or '?'} overlaps {c.kept.booked_by or '?'}"
                           for c in plan.conflicts])
    if plan.issues:
        n = len(plan.issues)
        lines.append(f"{n} row{'s' * (n != 1)} I couldn't read and left alone, please fix:")
        lines += _bullets([f"row {i.row}: {i.message}" for i in plan.issues])
    # Once a day, and again only if a later run that day removes something new.
    day = f"summary:{plan.now.date()}"
    key, covers = day, ()
    if plan.removals:
        removed = "|".join(sorted(f"{r.booking.room}{r.booking.when()}{r.booking.booked_by}".casefold()
                                  for r in plan.removals))
        key, covers = f"{day}:{hashlib.sha1(removed.encode()).hexdigest()[:10]}", (day,)
    return Message(key=key, kind="summary", text="\n".join(lines), covers=covers,
                   subject=f"Room bookings: morning reset, {plan.now:%a %d %b}")


def plan_messages(plan: Plan, cfg: Config, source_name: str, summary: bool = True) -> list[Message]:
    removed = plan.rows_to_remove
    msgs = [conflict_message(c, cfg, c.clashing.row in removed) for c in plan.conflicts]
    msgs += [no_show_message(r.booking, plan.now.date()) for r in plan.removals if r.kind == "no_show"]
    if summary and cfg.notifications.morning_summary and (s := summary_message(plan, source_name)):
        msgs.append(s)
    return msgs


def auto_releases(b: Booking, cfg: Config) -> bool:
    """Is this booking freed when nobody taps Keep? Only one-off, unprotected bookings are."""
    return bool(cfg.links and cfg.links.no_answer == "release" and not b.recurring
                and not is_protected(b, cfg))


def reminder_message(b: Booking, day: date, now: datetime, cfg: Config) -> Message:
    start = datetime.combine(day, b.start)
    minutes = max(1, round((start - now).total_seconds() / 60))
    text = f"Reminder: {b.room}, {_span(b)}{_title(b)}, starts in {minutes} minute{'s' * (minutes != 1)}."
    links: tuple = ()
    if cfg.links and not b.recurring:
        links = booking_links(cfg.links, b, day)
        if auto_releases(b, cfg):
            deadline = start + timedelta(minutes=cfg.links.answer_within)
            text += f" Tap Keep by {deadline:%H:%M}, or the room is released for others."
        else:
            text += " Not needed any more? Release it so others can use it."
    return Message(key=f"remind:{b.occurrence(day)}", kind="reminder", text=text, to=b.booked_by or None,
                   links=links, subject=f"{b.room} at {b.start:%H:%M}")


def no_show_message(b: Booking, day: date) -> Message:
    return Message(key=f"no_show:{b.occurrence(day)}", kind="no_show", to=b.booked_by or None,
                   text=f"Nobody tapped Keep for {b.room}, {_span(b)}{_title(b)}, so I released the room. "
                        "Need it after all? Add it back to the sheet.",
                   subject=f"{b.room} released")


def _hours(h: float) -> str:
    h = round(h * 2) / 2  # to the half hour
    return f"{h:g} h"


def weekly_message(entries: list[dict], now: datetime, source_name: str) -> Message | None:
    """The Friday post: what Ghostbooking did this week, from the audit log."""
    removed = [e for e in entries if e.get("event") == "removed" and isinstance(e.get("booking"), dict)]
    by_kind: dict[str, list[dict]] = {}
    for e in removed:
        by_kind.setdefault(e.get("kind", "ghost"), []).append(e)
    conflicts = sum(e.get("event") == "conflict" for e in entries)

    def span(e: dict) -> tuple[datetime, datetime] | None:
        b = e["booking"]
        try:
            day = date.fromisoformat(str(b["day"]))
            return (datetime.combine(day, datetime.strptime(b["start"], "%H:%M").time()),
                    datetime.combine(day, datetime.strptime(b["end"], "%H:%M").time()))
        except (KeyError, TypeError, ValueError):
            return None

    given_back = 0.0  # released early or no-show: the rest of the slot went back to the team
    for e in by_kind.get("released", []) + by_kind.get("no_show", []):
        if s := span(e):
            at = datetime.fromisoformat(e["at"])
            given_back += max(0.0, (s[1] - max(at, s[0])).total_seconds() / 3600)
    booked: dict[str, float] = {}  # meetings that took place, per room
    for e in by_kind.get("ended", []) + by_kind.get("ghost", []):
        if s := span(e):
            room = e["booking"]["room"]
            booked[room] = booked.get(room, 0.0) + (s[1] - s[0]).total_seconds() / 3600

    n = {k: len(v) for k, v in by_kind.items()}
    if not (removed or conflicts):
        return None
    lines = [f"This week in {source_name}:"]
    if n.get("ended"):
        lines.append(f"• {n['ended']} meeting{'s' * (n['ended'] != 1)} freed the room as soon as "
                     f"{'they' if n['ended'] != 1 else 'it'} ended")
    if n.get("ghost"):
        lines.append(f"• {n['ghost']} ghost booking{'s' * (n['ghost'] != 1)} from earlier days cleared")
    early = n.get("released", 0) + n.get("no_show", 0)
    if early:
        parts = [f"{n['released']} released early" if n.get("released") else "",
                 f"{n['no_show']} no-show{'s' * (n['no_show'] != 1)}" if n.get("no_show") else ""]
        lines.append(f"• {' and '.join(p for p in parts if p)}: {_hours(given_back)} of room time "
                     "given back")
    if conflicts:
        lines.append(f"• {conflicts} double booking{'s' * (conflicts != 1)} caught")
    if booked:
        room, hours = max(booked.items(), key=lambda kv: kv[1])
        lines.append(f"• Busiest room: {room}, {_hours(hours)} of meetings")
    year, week, _ = now.isocalendar()
    return Message(key=f"weekly:{year}-W{week:02d}", kind="weekly", text="\n".join(lines),
                   subject=f"Room bookings: week of {now - timedelta(days=now.weekday()):%d %b}")


def skipped_message(reason: str, source_name: str, now: datetime) -> Message:
    return Message(key=f"skipped:{now.date()}", kind="skipped",
                   text=f"I couldn't clean {source_name} this morning: {reason}. "
                        "Please close it when you're done; I'll try again at the next run.",
                   subject="Room bookings: morning reset skipped")
