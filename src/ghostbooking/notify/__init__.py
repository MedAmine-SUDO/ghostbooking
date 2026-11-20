"""Channels: console, Slack, email, Teams, Discord and a generic webhook."""

from __future__ import annotations

from collections.abc import Callable

import httpx
from rich.console import Console

from .. import config as c
from .base import Channel, ChannelError
from .console import ConsoleChannel
from .discord import DiscordChannel
from .email_smtp import EmailChannel
from .message import Link, Message
from .people import People
from .router import Delivery, Notifier, Outcome
from .slack import SlackChannel
from .teams import TeamsChannel
from .webhook import WebhookChannel


def build_notifier(cfg: c.Config, console: Console | None = None, http: httpx.Client | None = None,
                   smtp: Callable | None = None) -> Notifier:
    http = http or httpx.Client(timeout=15)
    channels: list[Channel] = []
    for ch in cfg.channels:
        match ch:
            case c.ConsoleChannel():
                channels.append(ConsoleChannel(console))
            case c.SlackChannel():
                channels.append(SlackChannel(ch, http))
            case c.EmailChannel():
                channels.append(EmailChannel(ch, smtp))
            case c.TeamsChannel():
                channels.append(TeamsChannel(ch, http))
            case c.DiscordChannel():
                channels.append(DiscordChannel(ch, http))
            case c.WebhookChannel():
                channels.append(WebhookChannel(ch, http))
    return Notifier(channels, People(cfg.people), cfg.state, cfg.log)


__all__ = ["Channel", "ChannelError", "Delivery", "Link", "Message", "Notifier", "Outcome",
           "People", "build_notifier"]
