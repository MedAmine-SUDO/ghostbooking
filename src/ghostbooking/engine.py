"""One pass: everything that is due right now. `tick` runs one, `run` runs one every minute.

A pass reads the sheet, frees what should be freed (the morning reset once a working day,
bookings that ended, Release links, no-shows), writes the sheet once, then sends what is due.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from rich.console import Console

from . import audit
from .config import Config
from .links import LinkData
from .messages import (
    auto_releases,
    plan_messages,
    reminder_message,
    skipped_message,
    weekly_message,
)
from .models import Sheet
from .notify import Delivery, Message, Outcome, build_notifier
from .parsing import parse_recurrence
from .rules import Plan, Removal, plan_reset
from .sources import Source, SourceBusy, open_source
from .state import State


@dataclass
class Pass:
    now: datetime
    is_reset: bool
    source_name: str
    plan: Plan
    messages: list[Message] = field(default_factory=list)
    planned: list[Delivery] = field(default_factory=list)  # dry run: what would be sent
    backup: Path | None = None
    skipped: str | None = None  # the file was busy; nothing was written
    outcome: Outcome | None = None

    @property
    def quiet(self) -> bool:
        """Nothing freed, nothing sent: the scheduler prints nothing for passes like this."""
        o = self.outcome
        went_out = o is not None and bool(o.sent or o.failed or o.undeliverable)
        return not (self.plan.removals or self.skipped or self.planned or went_out)


class Engine:
    def __init__(self, cfg: Config, console: Console | None = None, http: httpx.Client | None = None,
                 smtp: Callable | None = None):
        self.cfg = cfg
        self.state = State(cfg.state)
        self.notifier = build_notifier(cfg, console, http, smtp, self.state)
        self._source: Source | None = None
        self._lock = threading.Lock()  # one pass at a time: scheduler and link clicks share the file
        self._working_days = parse_recurrence(cfg.rules.working_days) or frozenset(range(5))

    def source(self) -> Source:
        if self._source is None:  # kept open: a Google Sheet would otherwise log in every pass
            self._source = open_source(self.cfg.source, self.cfg.backups, self.cfg.keep_backups)
        return self._source

    def reset_due(self, now: datetime) -> bool:
        return (now.weekday() in self._working_days and now.time() >= self.cfg.rules.reset_at
                and str(now.date()) not in self.state.section("resets"))

    # --- a pass ----------------------------------------------------------------------------
    def run_pass(self, now: datetime, dry_run: bool = False, force_reset: bool = False) -> Pass:
        with self._lock:
            return self._pass(now, dry_run, force_reset)

    def _pass(self, now: datetime, dry_run: bool, force_reset: bool) -> Pass:
        cfg = self.cfg
        source = self.source()
        sheet = source.read()
        is_reset = force_reset or self.reset_due(now)
        plan = plan_reset(sheet, cfg, now)
        if not is_reset and not cfg.rules.release_ended:
            plan.removals = [r for r in plan.removals if r.kind not in ("ghost", "ended")]
        plan.removals += self._answers(sheet, plan, now)

        reminders = self._reminders(sheet, plan, now) + self._weekly(now, source.name)
        p = Pass(now, is_reset, source.name, plan)
        p.messages = plan_messages(plan, cfg, source.name, summary=is_reset) + reminders
        if dry_run:
            p.planned = self.notifier.plan(p.messages)
            return p
        try:
            p.backup = source.remove_rows(plan.rows_to_remove)
        except SourceBusy as e:
            p.skipped = str(e)
            audit.record(cfg.log, "skipped", at=now, reason=p.skipped)
            # Reminders don't need the file. The rest waits until the file can be written.
            p.messages = reminders + ([skipped_message(p.skipped, p.source_name, now)] if is_reset else [])
            p.outcome = self._send(p.messages, now)
            return p
        self._record(plan, is_reset, now)
        p.outcome = self._send(p.messages, now)
        return p

    def _send(self, messages: list[Message], now: datetime) -> Outcome:
        outcome = self.notifier.send(messages)
        failed = {d.message.key for d, _ in outcome.failed}
        for m in messages:
            if m.kind == "weekly" and m.key not in failed:  # failed: try again next pass
                self.state.mark("weeklies", m.key.removeprefix("weekly:"))
        return outcome

    def _answers(self, sheet: Sheet, plan: Plan, now: datetime) -> list[Removal]:
        """Bookings released with a link, and no-shows nobody kept."""
        cfg, today = self.cfg, now.date()
        state = self.state.read()
        released, kept, sent = (state.get(k, {}) for k in ("released", "kept", "sent"))
        taken = plan.rows_to_remove
        out: list[Removal] = []
        for b in sheet.bookings:
            if b.recurring or b.row in taken:
                continue
            occ = b.occurrence(b.day)  # type: ignore[arg-type]
            start, end = b.span_on(b.day)  # type: ignore[arg-type]
            if occ in released:
                out.append(Removal(b, "released with the link", "released"))
            elif (b.day == today and auto_releases(b, cfg) and f"remind:{occ}" in sent
                  and occ not in kept and start + timedelta(minutes=cfg.links.answer_within) <= now < end):  # type: ignore[union-attr]
                deadline = start + timedelta(minutes=cfg.links.answer_within)  # type: ignore[union-attr]
                out.append(Removal(b, f"nobody tapped Keep by {deadline:%H:%M}", "no_show"))
        return out

    def _reminders(self, sheet: Sheet, plan: Plan, now: datetime) -> list[Message]:
        cfg, today = self.cfg, now.date()
        if cfg.reminders.before <= 0:
            return []
        lead = timedelta(minutes=cfg.reminders.before)
        out = []
        for b in sheet.bookings:
            if b.row in plan.rows_to_remove or not b.occurs_on(today):
                continue
            if b.recurring and not cfg.reminders.recurring:
                continue
            start = datetime.combine(today, b.start)
            if start - lead <= now < start:
                out.append(reminder_message(b, today, now, cfg))
        return out

    def _record(self, plan: Plan, is_reset: bool, now: datetime) -> None:
        log = self.cfg.log
        for r in plan.removals:
            audit.record(log, "removed", at=now, kind=r.kind, reason=r.reason,
                         booking=audit.booking_dict(r.booking))
        for c in plan.conflicts:
            if not self.notifier.was_sent(f"conflict:{c.clashing.occurrence(c.day)}"):
                audit.record(log, "conflict", at=now, day=c.day, kept=audit.booking_dict(c.kept),
                             clashing=audit.booking_dict(c.clashing), suggestion=c.suggestion)
        done = [r.booking.occurrence(r.booking.day) for r in plan.removals if r.kind == "released"]  # type: ignore[arg-type]
        if done:
            self.state.forget("released", *done)
        if is_reset:
            self.state.mark("resets", str(now.date()))

    def weekly_due(self, now: datetime) -> bool:
        when = self.cfg.notifications.weekly()
        if when is None:
            return False
        year, week, _ = now.isocalendar()
        return (now.weekday() == when[0] and now.time() >= when[1]
                and f"{year}-W{week:02d}" not in self.state.section("weeklies"))

    def _weekly(self, now: datetime, source_name: str) -> list[Message]:
        if not self.weekly_due(now):
            return []
        monday = datetime.combine(now.date() - timedelta(days=now.weekday()), datetime.min.time())
        msg = weekly_message(audit.read(self.cfg.log, monday, now), now, source_name)
        year, week, _ = now.isocalendar()
        if msg is None:  # a quiet week: nothing to say, and nothing to retry
            self.state.mark("weeklies", f"{year}-W{week:02d}")
            return []
        return [msg]

    # --- Keep / Release links --------------------------------------------------------------
    def answer(self, data: LinkData, now: datetime) -> tuple[str, str]:
        """Apply a Keep or Release click. Returns a (title, text) for the page shown afterwards."""
        occ, b = data.occurrence, data.booking()
        what = f"{b.room}, {data.day:%a %d %b} {data.start:%H:%M}–{data.end:%H:%M}"
        with self._lock:
            sheet = self.source().read()
            here = any(x.occurrence(data.day) == occ for x in sheet.bookings if not x.recurring)
            if occ in self.state.section("released") or not here:
                return "Already free", f"{what} isn't booked any more. Nothing to do."
            if data.action == "keep":
                self.state.mark("kept", occ)
                audit.record(self.cfg.log, "kept", at=now, booking=what, by=data.booked_by)
                return "Kept", f"{what} stays booked for you. Thanks for confirming."
            self.state.mark("released", occ)
            audit.record(self.cfg.log, "release_requested", at=now, booking=what, by=data.booked_by)
            p = self._pass(now, dry_run=False, force_reset=False)
        if p.skipped:
            return "Released", (f"Thanks, {what} is released. The sheet is open in Excel right now, "
                                "so it will disappear from the file as soon as it's closed.")
        return "Released", f"Thanks, {what} is free again for others."
