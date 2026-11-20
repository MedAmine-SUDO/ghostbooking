"""ghostbooking check | tick [--dry-run] [--now "2026-11-23 07:30"]

Exit codes: 0 ok, 1 config or file error, 2 skipped (file open, try later), 3 some messages failed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, audit
from .config import Config, load_config
from .messages import plan_messages, skipped_message
from .notify import Message, Notifier, build_notifier
from .rules import Plan, plan_reset
from .sources import SourceBusy, open_source

console = Console(soft_wrap=True)  # long lines stay whole when piped or logged


def _now(cfg: Config, override: str | None) -> datetime:
    if override:
        return datetime.fromisoformat(override)
    return datetime.now(ZoneInfo(cfg.timezone)).replace(tzinfo=None)


def _bookings_table(sheet, plan: Plan) -> Table:
    removing = {r.booking.row: r.reason for r in plan.removals}
    clashing = {c.clashing.row for c in plan.conflicts}
    protected = {b.row for b in plan.protected}
    t = Table(title="Bookings in the sheet", title_justify="left", header_style="bold")
    for col in ("Row", "Room", "When", "Booked by", "Meeting", "Status"):
        t.add_column(col)
    for b in sorted(sheet.bookings, key=lambda b: b.row):
        if b.row in removing:
            status = "[red]ghost[/]"
        elif b.row in clashing:
            status = "[yellow]double-booked[/]"
        elif b.row in protected:
            status = "[cyan]protected[/]"
        else:
            status = "[green]ok[/]"
        t.add_row(str(b.row), b.room, b.when(), b.booked_by, b.title, status)
    return t


def _print_plan(plan: Plan) -> None:
    if plan.removals:
        console.print(f"\n[bold]Ghost bookings to clear ({len(plan.removals)})[/]")
        for r in plan.removals:
            b = r.booking
            console.print(f"  [red]−[/] row {b.row}: {b.room}, {b.when()}, {b.booked_by or '?'} [dim]({r.reason})[/]")
    else:
        console.print("\n[green]No ghost bookings.[/]")
    if plan.conflicts:
        console.print(f"\n[bold]Double bookings ({len(plan.conflicts)})[/]")
        for c in plan.conflicts:
            tip = f" → {c.suggestion}" if c.suggestion else ""
            console.print(
                f"  [yellow]![/] {c.day:%a %d %b} {c.clashing.room}: row {c.clashing.row} "
                f"({c.clashing.booked_by or '?'}, {c.clashing.start:%H:%M}–{c.clashing.end:%H:%M}) "
                f"clashes with row {c.kept.row} ({c.kept.booked_by or '?'}){tip}"
            )
    if plan.issues:
        console.print(f"\n[bold]Rows I couldn't read ({len(plan.issues)})[/] [dim]left untouched[/]")
        for i in plan.issues:
            console.print(f"  [magenta]?[/] row {i.row}: {i.message}")


def _print_people(sheet, notifier: Notifier) -> None:
    names = sorted({b.booked_by.strip() for b in sheet.bookings if b.booked_by.strip()}, key=str.casefold)
    real = [c for c in notifier.channels if not c.mirror]
    console.print(f"\n[bold]Channels[/]: {', '.join(c.name for c in notifier.channels) or 'none'}")
    unknown, team_only = [], []
    for name in names:
        person = notifier.people.lookup(name)
        if person is None:
            unknown.append(name)
        elif real and not any(c.reaches(person) for c in real):
            team_only.append(name)
    if unknown:
        console.print(f"[yellow]Not in `people:`[/] ({len(unknown)}): {', '.join(unknown)} "
                      "[dim]· their messages go to the team channel[/]")
    if team_only:
        console.print(f"[yellow]No direct channel for[/]: {', '.join(team_only)} "
                      "[dim]· add a slack ID or email to DM them[/]")
    if real and not any(c.has_team() for c in real):
        console.print("[yellow]No team channel set up:[/] summaries and messages for unknown people "
                      "won't reach anyone.")
    if not unknown and not team_only:
        console.print(f"[green]Everyone in the sheet can be reached[/] ({len(names)} people).")


def _print_deliveries(notifier: Notifier, messages: list[Message]) -> None:
    planned = notifier.plan(messages)
    deliveries = [d for d in planned if not d.channel.mirror] or planned  # console only: show those
    if not deliveries:
        console.print("\n[dim]No messages to send.[/]")
        return
    console.print(f"\n[bold]Messages it would send ({len(deliveries)})[/]")
    for d in deliveries:
        console.print(f"  [bold]✉ {escape(d.describe())}[/] [dim]· {d.message.kind}[/]")
        for line in d.message.text.splitlines():
            console.print(f"    {escape(line)}")


def _report(outcome) -> int:
    real = [d for d in outcome.sent if not d.channel.mirror]
    if real:
        console.print(f"\n[green]Sent {len(real)} message(s).[/]")
    for m in outcome.undeliverable:
        console.print(f"[yellow]Nobody could be reached for a {m.kind} message[/] "
                      "[dim](set up a team channel)[/]")
    for d, err in outcome.failed:
        console.print(f"[red]Failed:[/] {escape(d.describe())}: {escape(err)}")
    return 3 if outcome.failed else 0


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    source = open_source(cfg.source, cfg.backups, cfg.keep_backups)
    sheet = source.read()
    now = _now(cfg, args.now)
    plan = plan_reset(sheet, cfg, now)
    console.print(f"[bold]Ghostbooking {__version__}[/] · {source.path.name} · now {now:%a %d %b %H:%M} ({cfg.timezone})\n")
    console.print(_bookings_table(sheet, plan))
    _print_plan(plan)
    _print_people(sheet, build_notifier(cfg, console))
    if source.is_open_elsewhere():
        console.print("\n[yellow]The file is open in Excel right now; `tick` will wait until it's closed.[/]")
    console.print("\n[green]Config OK.[/] Run [bold]ghostbooking tick --dry-run[/] to preview the morning reset.")
    return 0


def cmd_tick(args) -> int:
    cfg = load_config(args.config)
    source = open_source(cfg.source, cfg.backups, cfg.keep_backups)
    sheet = source.read()
    now = _now(cfg, args.now)
    plan = plan_reset(sheet, cfg, now)
    console.print(f"[bold]Morning reset[/] · {source.path.name} · {now:%a %d %b %H:%M}")
    _print_plan(plan)
    notifier = build_notifier(cfg, console)
    messages = plan_messages(plan, cfg, source.path.name)
    if args.dry_run:
        _print_deliveries(notifier, messages)
        console.print("\n[dim]Dry run: nothing was written or sent.[/]")
        return 0
    try:
        backup = source.remove_rows(plan.rows_to_remove)
    except SourceBusy as e:
        console.print(f"\n[yellow]Skipped: {e}.[/]")
        audit.record(cfg.log, "skipped", reason=str(e))
        _report(notifier.send([skipped_message(str(e), source.path.name, now)]))
        return 2
    for r in plan.removals:
        audit.record(cfg.log, "removed", reason=r.reason, booking=audit.booking_dict(r.booking))
    for c in plan.conflicts:
        audit.record(cfg.log, "conflict", day=c.day, kept=audit.booking_dict(c.kept),
                     clashing=audit.booking_dict(c.clashing), suggestion=c.suggestion)
    if backup:
        console.print(f"\n[green]Cleared {len(plan.removals)} ghost booking(s).[/] Backup: {backup}")
    return _report(notifier.send(messages))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ghostbooking", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (("check", cmd_check, "read the sheet and show what Ghostbooking sees"),
                            ("tick", cmd_tick, "run the morning reset once")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("-c", "--config", default="config.yaml")
        s.add_argument("--now", help='pretend it is this time, e.g. "2026-11-23 07:30"')
        if name == "tick":
            s.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
        s.set_defaults(fn=fn)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
