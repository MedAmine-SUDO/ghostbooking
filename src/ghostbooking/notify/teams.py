from __future__ import annotations

import httpx

from ..config import Person
from ..config import TeamsChannel as TeamsConfig
from .base import Channel, ChannelError, who
from .message import Message


class TeamsChannel(Channel):
    """A Teams Workflows webhook posting an Adaptive Card to a channel; people are named in bold."""

    name = "teams"

    def __init__(self, cfg: TeamsConfig, http: httpx.Client):
        self.cfg = cfg
        self.http = http

    def has_team(self) -> bool:
        return True

    def card(self, msg: Message, about: Person | str | None) -> dict:
        body: list[dict] = []
        if about:
            body.append({"type": "TextBlock", "text": f"For **{who(about)}**:", "wrap": True})
        body += [{"type": "TextBlock", "text": line, "wrap": True} for line in msg.text.splitlines() if line]
        return {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": [{"type": "Action.OpenUrl", "title": link.label, "url": link.url}
                                for link in msg.links],
                },
            }],
        }

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        try:
            r = self.http.post(self.cfg.webhook_url, json=self.card(msg, about))
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ChannelError(f"teams: {e}") from e
