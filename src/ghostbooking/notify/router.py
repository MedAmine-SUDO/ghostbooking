"""Decide which channel carries each message, send it, and remember what was sent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .. import audit
from ..config import Person
from .base import Channel, ChannelError, who
from .message import Message
from .people import People

KEEP_STATE_DAYS = 60


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
    def __init__(self, channels: list[Channel], people: People, state: Path, log: Path):
        self.channels = channels
        self.people = people
        self.state_path = Path(state)
        self.log = Path(log)

    # --- state -----------------------------------------------------------------------------
    def _load(self) -> dict[str, str]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8")).get("sent", {})
        except (FileNotFoundError, ValueError):
            return {}

    def _save(self, sent: dict[str, str]) -> None:
        cutoff = (datetime.now() - timedelta(days=KEEP_STATE_DAYS)).isoformat()
        sent = {k: v for k, v in sent.items() if v >= cutoff}
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"sent": sent}, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

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
        sent = self._load()
        return [d for m in messages if m.key not in sent for d in self.route(m)]

    def unrouted(self, messages: list[Message]) -> list[Message]:
        """Messages only a mirror (the console) would show: nobody would actually get them."""
        if all(c.mirror for c in self.channels):
            return []  # console only: a trial run, nothing is supposed to go out
        sent = self._load()
        return [m for m in messages if m.key not in sent
                and all(d.channel.mirror for d in self.route(m))]

    def send(self, messages: list[Message]) -> Outcome:
        sent_keys = self._load()
        outcome = Outcome([], [], self.unrouted(messages))
        for d in self.plan(messages):
            try:
                d.channel.send(d.message, d.to, d.about)
            except ChannelError as e:
                outcome.failed.append((d, str(e)))
                audit.record(self.log, "send_failed", key=d.message.key, route=d.describe(), error=str(e))
                continue
            outcome.sent.append(d)
            if not d.channel.mirror:
                stamp = datetime.now().isoformat(timespec="seconds")
                sent_keys.update(dict.fromkeys([d.message.key, *d.message.covers], stamp))
                audit.record(self.log, "sent", key=d.message.key, kind=d.message.kind, route=d.describe())
        if any(not c.mirror for c in self.channels):
            self._save(sent_keys)
        return outcome
