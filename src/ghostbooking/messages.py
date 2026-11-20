"""Turn a reset plan into messages. Wording lives here, channels only deliver it.

Text is light Markdown: **bold** and blank lines between sections. Each channel turns it into its own
format (Slack *bold*, plain text for e-mail); see notify/message.py.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta

from .config import Config
from .links import booking_links
from .models import Booking
from .notify import Link, Message
from .rules import Conflict, Plan, is_protected

MAX_LINES = 10  # longer lists in the team summary end with "and N more"


def _span(b: Booking) -> str:
    return f"{b.start:%H:%M}–{b.end:%H:%M}"


def _title(b: Booking) -> str:
    return f" · {b.title}" if b.title else ""


def _n(count: int, word: str, plural: str | None = None) -> str:
    return f"{count} {word if count == 1 else plural or word + 's'}"


def sheet_links(cfg: Config) -> tuple[Link, ...]:
    """Where people open the sheet, when the config says so (`source.url`)."""
    return (Link("Open the sheet", cfg.source.url),) if cfg.source.url else ()


def conflict_message(c: Conflict, cfg: Config, removed: bool) -> Message:
    b, kept = c.clashing, c.kept
    owner = kept.booked_by or "Someone"
    why = "It's a protected meeting." if is_protected(kept, cfg) else "They booked first."
    where = f"{b.room}, {c.day:%a %d %b} {_span(b)}"
    head = (f"❌ **Your booking was removed: {where}**" if removed
            else f"⚠️ **Double booking: {where}**") + _title(b)
    tip = f"💡 {c.suggestion}." if c.suggestion else "No other room is free at that time."
    text = f"{head}\n{owner} has {b.room} {_span(kept)}. {why}\n{tip}"
    return Message(key=f"conflict:{b.occurrence(c.day)}", kind="conflict", text=text, to=b.booked_by or None,
                   subject=f"Double booking: {b.room}, {c.day:%a %d %b} {_span(b)}")


def _bullets(lines: list[str]) -> list[str]:
    shown = [f"• {line}" for line in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        shown.append(f"• and {len(lines) - MAX_LINES} more")
    return shown


def summary_message(plan: Plan, source_name: str, cfg: Config | None = None) -> Message | None:
    if not (plan.removals or plan.conflicts or plan.issues):
        return None
    lines = [f"🌅 **Morning reset · {source_name} · {plan.now:%a %d %b}**"]
    if plan.removals:
        lines += ["", f"🧹 **{_n(len(plan.removals), 'old booking')} cleared**"]
        lines += _bullets([f"{r.booking.room} · {r.booking.when()} · {r.booking.booked_by or '?'}"
                           for r in plan.removals])
    if plan.conflicts:
        lines += ["", f"⚠️ **{_n(len(plan.conflicts), 'double booking')}** · the people involved were told"]
        lines += _bullets([f"{c.clashing.room} · {c.day:%a %d %b} {_span(c.clashing)} · "
                           f"{c.clashing.booked_by or '?'} overlaps {c.kept.booked_by or '?'}"
                           for c in plan.conflicts])
    if plan.issues:
        lines += ["", f"✏️ **{_n(len(plan.issues), 'row')} to fix** · I couldn't read {'it' if len(plan.issues) == 1 else 'them'}, so I left {'it' if len(plan.issues) == 1 else 'them'} alone"]
        lines += _bullets([f"Row {i.row} · {i.message}" for i in plan.issues])
    # Once a day, and again only if a later run that day removes something new.
    day = f"summary:{plan.now.date()}"
    key, covers = day, ()
    if plan.removals:
        removed = "|".join(sorted(f"{r.booking.room}{r.booking.when()}{r.booking.booked_by}".casefold()
                                  for r in plan.removals))
        key, covers = f"{day}:{hashlib.sha1(removed.encode()).hexdigest()[:10]}", (day,)
    return Message(key=key, kind="summary", text="\n".join(lines), covers=covers,
                   links=sheet_links(cfg) if cfg else (),
                   subject=f"Room bookings: morning reset, {plan.now:%a %d %b}")


def plan_messages(plan: Plan, cfg: Config, source_name: str, summary: bool = True) -> list[Message]:
    removed = plan.rows_to_remove
    msgs = [conflict_message(c, cfg, c.clashing.row in removed) for c in plan.conflicts]
    msgs += [no_show_message(r.booking, plan.now.date(), cfg) for r in plan.removals if r.kind == "no_show"]
    if summary and cfg.notifications.morning_summary and (s := summary_message(plan, source_name, cfg)):
        msgs.append(s)
    return msgs


def auto_releases(b: Booking, cfg: Config) -> bool:
    """Is this booking freed when nobody taps Keep? Only one-off, unprotected bookings are."""
    return bool(cfg.links and cfg.links.no_answer == "release" and not b.recurring
                and not is_protected(b, cfg))


def reminder_message(b: Booking, day: date, now: datetime, cfg: Config) -> Message:
    start = datetime.combine(day, b.start)
    minutes = max(1, round((start - now).total_seconds() / 60))
    text = f"⏰ **{b.room} · {_span(b)}**{_title(b)}\nStarts in {_n(minutes, 'minute')}."
    links: tuple = ()
    if cfg.links and not b.recurring:
        links = booking_links(cfg.links, b, day)
        if auto_releases(b, cfg):
            deadline = start + timedelta(minutes=cfg.links.answer_within)
            text += f" Tap **Keep** by {deadline:%H:%M}, or the room goes back to the team."
        else:
            text += " Not needed any more? Release it so others can use it."
    return Message(key=f"remind:{b.occurrence(day)}", kind="reminder", text=text, to=b.booked_by or None,
                   links=links, subject=f"{b.room} at {b.start:%H:%M}")


def no_show_message(b: Booking, day: date, cfg: Config | None = None) -> Message:
    return Message(key=f"no_show:{b.occurrence(day)}", kind="no_show", to=b.booked_by or None,
                   text=f"🔓 **{b.room} · {_span(b)} released**{_title(b)}\n"
                        "Nobody tapped Keep, so the room went back to the team. "
                        "Need it after all? Add it back to the sheet.",
                   links=sheet_links(cfg) if cfg else (), subject=f"{b.room} released")


def _hours(h: float) -> str:
    h = round(h * 2) / 2  # to the half hour
    return f"{h:g} h"


def weekly_message(entries: list[dict], now: datetime, source_name: str,
                   links: tuple[Link, ...] = ()) -> Message | None:
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
    monday = now - timedelta(days=now.weekday())
    lines = [f"📊 **Week of {monday:%d %b} · {source_name}**"]
    early = n.get("released", 0) + n.get("no_show", 0)
    if early:
        parts = [f"{n['released']} released early" if n.get("released") else "",
                 _n(n["no_show"], "no-show") if n.get("no_show") else ""]
        lines += ["", f"⏱️ **{_hours(given_back)} of room time given back**", ", ".join(p for p in parts if p)]
    cleaned = n.get("ended", 0) + n.get("ghost", 0)
    if cleaned:
        parts = [f"{n['ended']} as soon as {'it' if n['ended'] == 1 else 'they'} ended" if n.get("ended") else "",
                 f"{n['ghost']} left over from earlier days" if n.get("ghost") else ""]
        lines += ["", f"🧹 **{_n(cleaned, 'old booking')} cleaned up**", ", ".join(p for p in parts if p)]
    if conflicts:
        lines += ["", f"⚠️ **{_n(conflicts, 'double booking')} caught**"]
    if booked:
        room, hours = max(booked.items(), key=lambda kv: kv[1])
        lines += ["", f"🏆 **Busiest room: {room}** · {_hours(hours)} of meetings"]
    year, week, _ = now.isocalendar()
    return Message(key=f"weekly:{year}-W{week:02d}", kind="weekly", text="\n".join(lines), links=links,
                   subject=f"Room bookings: week of {monday:%d %b}")


def skipped_message(reason: str, source_name: str, now: datetime, links: tuple[Link, ...] = ()) -> Message:
    return Message(key=f"skipped:{now.date()}", kind="skipped", links=links,
                   text=f"⏸️ **Morning reset skipped · {source_name}**\n{reason}. "
                        "Please close it when you're done; I'll try again at the next run.",
                   subject="Room bookings: morning reset skipped")
