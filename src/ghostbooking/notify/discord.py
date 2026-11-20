from __future__ import annotations

import httpx

from ..config import DiscordChannel as DiscordConfig
from ..config import Person
from .base import Channel, ChannelError, who
from .message import Message

LIMIT = 2000  # Discord rejects longer messages


class DiscordChannel(Channel):
    """A channel webhook. It can't DM, so everything goes to the channel with a mention."""

    name = "discord"

    def __init__(self, cfg: DiscordConfig, http: httpx.Client):
        self.cfg = cfg
        self.http = http

    def has_team(self) -> bool:
        return True

    def render(self, msg: Message, about: Person | str | None) -> tuple[str, list[str]]:
        users: list[str] = []
        text = msg.text
        if isinstance(about, Person) and about.discord:
            text, users = f"<@{about.discord}> {text}", [about.discord]
        elif about:
            text = f"**{who(about)}** {text}"
        if msg.links:
            text += "\n" + " · ".join(f"[{link.label}](<{link.url}>)" for link in msg.links)
        return (text if len(text) <= LIMIT else text[: LIMIT - 1] + "…"), users

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        text, users = self.render(msg, about)
        try:
            r = self.http.post(self.cfg.webhook_url,
                               json={"content": text, "allowed_mentions": {"users": users}})
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ChannelError(f"discord: {e}") from e
