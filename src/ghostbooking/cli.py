"""ghostbooking check | tick [--dry-run] [--reset] [--now "2026-11-23 07:30"] | run

Exit codes: 0 ok, 1 config or file error, 2 skipped (file open, try later), 3 some messages failed.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__, audit, web
from .config import Config, load_config
from .engine import Engine, Pass
from .notify import Delivery, Notifier, build_notifier
from .rules import Plan, plan_reset
from .sources import open_source

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


def _print_plan(plan: Plan, verbose: bool = True) -> None:
    if plan.removals:
        ghosts = all(r.kind == "ghost" for r in plan.removals)
        label = "Ghost bookings to clear" if ghosts else "Bookings to free"
        console.print(f"\n[bold]{label} ({len(plan.removals)})[/]")
        for r in plan.removals:
            b = r.booking
            console.print(f"  [red]−[/] row {b.row}: {b.room}, {b.when()}, {b.booked_by or '?'} [dim]({r.reason})[/]")
    elif verbose:
        console.print("\n[green]No ghost bookings.[/]")
    if plan.conflicts and verbose:
        console.print(f"\n[bold]Double bookings ({len(plan.conflicts)})[/]")
        for c in plan.conflicts:
            tip = f" → {c.suggestion}" if c.suggestion else ""
            console.print(
                f"  [yellow]![/] {c.day:%a %d %b} {c.clashing.room}: row {c.clashing.row} "
                f"({c.clashing.booked_by or '?'}, {c.clashing.start:%H:%M}–{c.clashing.end:%H:%M}) "
                f"clashes with row {c.kept.row} ({c.kept.booked_by or '?'}){tip}"
            )
    if plan.issues and verbose:
        console.print(f"\n[bold]Rows I couldn't read ({len(plan.issues)})[/] [dim]left untouched[/]")
        for i in plan.issues:
            console.print(f"  [magenta]?[/] row {i.row}: {i.message}")


def _reminders_line(cfg: Config) -> str:
    if cfg.reminders.before <= 0:
        return "off"
    line = f"{cfg.reminders.before} min before"
    if not cfg.links:
        return line + ", no Keep/Release links"
    after = ("released" if cfg.links.no_answer == "release" else "kept")
    return (f"{line}, with Keep/Release links via {cfg.links.base_url}; "
            f"{after} if nobody taps Keep within {cfg.links.answer_within} min")


def _print_people(cfg: Config, sheet, notifier: Notifier) -> None:
    names = sorted({b.booked_by.strip() for b in sheet.bookings if b.booked_by.strip()}, key=str.casefold)
    real = [c for c in notifier.channels if not c.mirror]
    console.print(f"\n[bold]Channels[/]: {', '.join(c.name for c in notifier.channels) or 'none'}")
    console.print(f"[bold]Reminders[/]: {_reminders_line(cfg)}")
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


def _print_deliveries(planned: list[Delivery]) -> None:
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
    _print_people(cfg, sheet, build_notifier(cfg, console))
    if source.is_open_elsewhere():
        console.print("\n[yellow]The file is open in Excel right now; `tick` will wait until it's closed.[/]")
    console.print("\n[green]Config OK.[/] Run [bold]ghostbooking tick --dry-run[/] to preview what's due now.")
    return 0


def _print_pass(p: Pass, dry_run: bool, verbose: bool = True) -> int:
    if not verbose and p.quiet:
        return 0
    label = "Morning reset" if p.is_reset else "Scan"
    console.print(f"[bold]{label}[/] · {p.source_name} · {p.now:%a %d %b %H:%M}")
    _print_plan(p.plan, verbose=p.is_reset)
    if dry_run:
        _print_deliveries(p.planned)
        console.print("\n[dim]Dry run: nothing was written or sent.[/]")
        return 0
    code = 0
    if p.skipped:
        console.print(f"\n[yellow]Skipped: {escape(p.skipped)}.[/] [dim]Trying again at the next run.[/]")
        code = 2
    elif p.backup:
        console.print(f"\n[green]Freed {len(p.plan.removals)} booking(s).[/] Backup: {p.backup}")
    if p.outcome:
        code = _report(p.outcome) or code
    return code


def cmd_tick(args) -> int:
    cfg = load_config(args.config)
    p = Engine(cfg, console).run_pass(_now(cfg, args.now), args.dry_run, force_reset=args.reset)
    return _print_pass(p, args.dry_run)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    engine = Engine(cfg, console)

    def clock() -> datetime:
        return _now(cfg, None)

    server = None
    if cfg.links:
        server = web.start(engine, clock)
        host, port = cfg.links.address()
        console.print(f"Keep/Release links: listening on {host}:{port}, public at {cfg.links.base_url}")
    else:
        console.print("[dim]No `links:` in config: reminders go out without Keep/Release links.[/]")
    console.print(f"Watching {cfg.source.path.name} every {args.every}s. Ctrl+C to stop.\n")
    try:
        while True:
            try:
                _print_pass(engine.run_pass(clock()), dry_run=False, verbose=False)
            except (OSError, ValueError) as e:  # e.g. the shared drive is offline: try again later
                console.print(f"[red]Error:[/] {escape(str(e))}")
                audit.record(cfg.log, "error", error=str(e))
            time.sleep(args.every - time.time() % args.every)
    except KeyboardInterrupt:
        console.print("\nStopped.")
    finally:
        if server:
            server.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ghostbooking", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    commands = (("check", cmd_check, "read the sheet and show what Ghostbooking sees"),
                ("tick", cmd_tick, "do whatever is due now, once (for cron)"),
                ("run", cmd_run, "keep running: a pass every minute, plus the Keep/Release links"))
    for name, fn, help_ in commands:
        s = sub.add_parser(name, help=help_)
        s.add_argument("-c", "--config", default="config.yaml")
        if name == "run":
            s.add_argument("--every", type=int, default=60, help="seconds between passes (default 60)")
        else:
            s.add_argument("--now", help='pretend it is this time, e.g. "2026-11-23 07:30"')
        if name == "tick":
            s.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
            s.add_argument("--reset", action="store_true", help="run the morning reset now, even if done today")
        s.set_defaults(fn=fn)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]Error:[/] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
