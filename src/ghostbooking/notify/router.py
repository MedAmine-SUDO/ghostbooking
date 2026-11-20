"""Decide which channel carries each message, send it, and remember what was sent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .. import audit
from ..config import Person
from ..state import State
from .base import Channel, ChannelError, who
from .message import Message
from .people import People


@dataclass(frozen=True)
class Delivery:
    channel: Channel
    message: Message
    to: Person | None  # None = the team channel
    about: Person | str | None = None

    def describe(self) -> str:
        if self.to:
            return f"{self.channel.name} → {self.to.name}"
        return f"{self.channel.name} → team" + (f" (for {who(self.about)})" if self.about else "")


@dataclass
class Outcome:
    sent: list[Delivery]
    failed: list[tuple[Delivery, str]]
    undeliverable: list[Message]  # no channel could carry it


class Notifier:
    def __init__(self, channels: list[Channel], people: People, state: State, log: Path):
        self.channels = channels
        self.people = people
        self.state = state
        self.log = Path(log)

    def was_sent(self, key: str) -> bool:
        return key in self.state.section("sent")

    # --- routing ---------------------------------------------------------------------------
    def route(self, msg: Message) -> list[Delivery]:
        real = [c for c in self.channels if not c.mirror]
        person = self.people.lookup(msg.to)
        out = [Delivery(c, msg, person, None if person else msg.to) for c in self.channels if c.mirror]
        direct = [c for c in real if person and c.reaches(person)]
        if direct:
            return out + [Delivery(c, msg, person) for c in direct]
        about = person or msg.to  # unknown or unreachable people get a mention in the team channel
        return out + [Delivery(c, msg, None, about) for c in real if c.has_team()]

    def plan(self, messages: list[Message]) -> list[Delivery]:
        sent = self.state.section("sent")
        return [d for m in messages if m.key not in sent for d in self.route(m)]

    def unrouted(self, messages: list[Message]) -> list[Message]:
        """Messages only a mirror (the console) would show: nobody would actually get them."""
        if all(c.mirror for c in self.channels):
            return []  # console only: a trial run, nothing is supposed to go out
        sent = self.state.section("sent")
        return [m for m in messages if m.key not in sent
                and all(d.channel.mirror for d in self.route(m))]

    def send(self, messages: list[Message]) -> Outcome:
        outcome = Outcome([], [], self.unrouted(messages))
        for d in self.plan(messages):
            try:
                d.channel.send(d.message, d.to, d.about)
            except ChannelError as e:
                outcome.failed.append((d, str(e)))
                audit.record(self.log, "send_failed", key=d.message.key, route=d.describe(), error=str(e))
                continue
            outcome.sent.append(d)
            if not d.channel.mirror:  # console-only runs are trials: nothing counts as sent
                self.state.mark("sent", d.message.key, *d.message.covers)
                audit.record(self.log, "sent", key=d.message.key, kind=d.message.kind, route=d.describe())
        return outcome
