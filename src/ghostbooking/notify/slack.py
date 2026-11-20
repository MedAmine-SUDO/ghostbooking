from __future__ import annotations

import httpx

from ..config import Person
from ..config import SlackChannel as SlackConfig
from .base import Channel, ChannelError, who
from .message import Message

API = "https://slack.com/api/chat.postMessage"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _mention(about: Person | str | None) -> str:
    if isinstance(about, Person) and about.slack:
        return f"<@{about.slack}>"
    return f"*{_escape(who(about))}*" if about else ""


class SlackChannel(Channel):
    """Bot token: DMs people and posts to `team_channel`. Incoming webhook: posts to its channel."""

    name = "slack"

    def __init__(self, cfg: SlackConfig, http: httpx.Client):
        self.cfg = cfg
        self.http = http

    def reaches(self, person: Person) -> bool:
        return bool(self.cfg.token and person.slack)

    def has_team(self) -> bool:
        return bool(self.cfg.webhook_url or (self.cfg.token and self.cfg.team_channel))

    def render(self, msg: Message, about: Person | str | None) -> str:
        text = _escape(msg.text)
        if about:
            text = f"{_mention(about)} {text}"
        if msg.links:
            text += "\n" + "  ·  ".join(f"<{link.url}|{_escape(link.label)}>" for link in msg.links)
        return text

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        text = self.render(msg, None if to else about)
        try:
            if to is None and not (self.cfg.token and self.cfg.team_channel):
                r = self.http.post(self.cfg.webhook_url, json={"text": text})  # type: ignore[arg-type]
                r.raise_for_status()
                return
            channel = to.slack if to else self.cfg.team_channel
            r = self.http.post(API, headers={"Authorization": f"Bearer {self.cfg.token}"},
                               json={"channel": channel, "text": text, "unfurl_links": False})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ChannelError(f"slack: {e}") from e
        if not data.get("ok"):
            raise ChannelError(f"slack: {data.get('error', 'unknown error')}")
