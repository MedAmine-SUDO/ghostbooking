"""Decide what to clean. Pure functions: bookings + config + now in, a plan out. Nothing is written here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from .config import Config, ProtectedRule
from .models import Booking, Issue, Sheet


@dataclass(frozen=True)
class Removal:
    booking: Booking
    reason: str


@dataclass(frozen=True)
class Conflict:
    day: date
    kept: Booking
    clashing: Booking
    suggestion: str | None


@dataclass
class Plan:
    now: datetime
    removals: list[Removal] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    protected: list[Booking] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def rows_to_remove(self) -> set[int]:
        return {r.booking.row for r in self.removals}


def _same(a: str, b: str) -> bool:
    return a.strip().casefold() == b.strip().casefold()


def rule_matches(rule: ProtectedRule, b: Booking) -> bool:
    if rule.title_contains and rule.title_contains.casefold() not in b.title.casefold():
        return False
    if rule.room and not _same(rule.room, b.room):
        return False
    if rule.booked_by and not _same(rule.booked_by, b.booked_by):
        return False
    if rule.start and rule.start != b.start:
        return False
    days = rule.weekday_set()
    if days is not None:
        booking_days = b.weekdays if b.recurring else frozenset({b.day.weekday()})  # type: ignore[union-attr]
        if not booking_days & days:
            return False
    return any([rule.title_contains, rule.room, rule.booked_by, rule.start, days])


def is_protected(b: Booking, cfg: Config) -> bool:
    return b.recurring or b.protected or any(rule_matches(r, b) for r in cfg.protected)


def _overlaps(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _free_slot(day: date, room: str, length, busy: list[tuple[datetime, datetime]],
               after: datetime, day_end: time) -> tuple[datetime, datetime] | None:
    start = after
    for s, e in sorted(busy):
        if e <= start:
            continue
        if s >= start + length:
            break
        start = max(start, e)
    end = start + length
    return (start, end) if end <= datetime.combine(day, day_end) else None


def plan_reset(sheet: Sheet, cfg: Config, now: datetime) -> Plan:
    plan = Plan(now=now, issues=list(sheet.issues))
    today = now.date()
    remaining: list[Booking] = []

    # 1. Ghost bookings: one-off bookings that have already ended.
    for b in sheet.bookings:
        protected = is_protected(b, cfg)
        if protected:
            plan.protected.append(b)
        if not b.recurring and datetime.combine(b.day, b.end) <= now and not protected:  # type: ignore[arg-type]
            reason = (f"ended at {b.end:%H:%M} today" if b.day == today
                      else f"left over from {b.day:%a %d %b}")
            plan.removals.append(Removal(b, reason))
        else:
            remaining.append(b)

    # 2. Double bookings, from today on. Protected meetings win, then whoever booked first.
    rooms = sorted({b.room.strip() for b in remaining}, key=str.casefold)
    days = sorted({b.day for b in remaining if not b.recurring and b.day >= today} | {today})  # type: ignore[operator]
    removed = set()
    for day in days:
        on_day = [b for b in remaining if b.occurs_on(day)]
        accepted: dict[str, list[Booking]] = {}
        order = sorted(on_day, key=lambda b: (not is_protected(b, cfg), b.booked_at or datetime.max, b.row))
        for b in order:
            key = b.room.strip().casefold()
            span = b.span_on(day)
            clash = next((a for a in accepted.get(key, []) if _overlaps(a.span_on(day), span)), None)
            if clash is None:
                accepted.setdefault(key, []).append(b)
                continue
            plan.conflicts.append(Conflict(day, clash, b, _suggest(b, day, accepted, rooms, cfg)))
            if cfg.rules.conflicts == "remove" and not is_protected(b, cfg) and b.row not in removed:
                removed.add(b.row)
                plan.removals.append(Removal(b, f"double-booked with row {clash.row} ({clash.booked_by or 'someone'})"))
    return plan


def _suggest(b: Booking, day: date, accepted: dict[str, list[Booking]], rooms: list[str], cfg: Config) -> str | None:
    start, end = b.span_on(day)
    length = end - start
    # Another room free at the same time?
    for room in rooms:
        if _same(room, b.room):
            continue
        busy = [a.span_on(day) for a in accepted.get(room.casefold(), [])]
        if not any(_overlaps(s, (start, end)) for s in busy):
            return f"{room} is free {start:%H:%M}–{end:%H:%M}"
    # Same room, later that day?
    busy = [a.span_on(day) for a in accepted.get(b.room.strip().casefold(), [])]
    slot = _free_slot(day, b.room, length, busy, start, cfg.rules.day_end)
    if slot:
        return f"{b.room} is free {slot[0]:%H:%M}–{slot[1]:%H:%M}"
    return None
