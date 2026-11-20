from __future__ import annotations

from rich.console import Console
from rich.markup import escape

from ..config import Person
from .base import Channel, who
from .message import Message, bold


class ConsoleChannel(Channel):
    """Prints messages to the terminal. Handy for trying things out and for logs."""

    name = "console"
    mirror = True

    def __init__(self, console: Console | None = None):
        self.console = console or Console(soft_wrap=True)

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        target = f"to {to.name}" if to else "to the team" + (f" (for {who(about)})" if about else "")
        self.console.print(f"\n  [bold]✉ {target}[/] [dim]· {msg.kind}[/]")
        for line in msg.text.splitlines():
            self.console.print(f"    {bold(escape(line), '[bold]', '[/bold]')}")
        for link in msg.links:
            self.console.print(f"    [link={link.url}]{escape(link.label)}[/link]: {escape(link.url)}")
