"""ghostbooking check | tick [--dry-run] [--now "2026-11-23 07:30"]"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.table import Table

from . import __version__, audit
from .config import Config, load_config
from .rules import Plan, plan_reset
from .sources import SourceBusy, open_source

console = Console()


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


def cmd_check(args) -> int:
    cfg = load_config(args.config)
    source = open_source(cfg.source, cfg.backups, cfg.keep_backups)
    sheet = source.read()
    now = _now(cfg, args.now)
    plan = plan_reset(sheet, cfg, now)
    console.print(f"[bold]Ghostbooking {__version__}[/] · {source.path.name} · now {now:%a %d %b %H:%M} ({cfg.timezone})\n")
    console.print(_bookings_table(sheet, plan))
    _print_plan(plan)
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
    if args.dry_run:
        console.print("\n[dim]Dry run: nothing was written.[/]")
        return 0
    try:
        backup = source.remove_rows(plan.rows_to_remove)
    except SourceBusy as e:
        console.print(f"\n[yellow]Skipped: {e}.[/]")
        audit.record(cfg.log, "skipped", reason=str(e))
        return 2
    for r in plan.removals:
        audit.record(cfg.log, "removed", reason=r.reason, booking=audit.booking_dict(r.booking))
    for c in plan.conflicts:
        audit.record(cfg.log, "conflict", day=c.day, kept=audit.booking_dict(c.kept),
                     clashing=audit.booking_dict(c.clashing), suggestion=c.suggestion)
    if backup:
        console.print(f"\n[green]Cleared {len(plan.removals)} ghost booking(s).[/] Backup: {backup}")
    return 0


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
