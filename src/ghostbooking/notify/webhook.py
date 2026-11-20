from __future__ import annotations

import httpx

from ..config import Person
from ..config import WebhookChannel as WebhookConfig
from .base import Channel, ChannelError, who
from .message import Message, plain


class WebhookChannel(Channel):
    """POSTs every message as JSON. The receiver (n8n, Make, Zapier...) decides where it goes."""

    name = "webhook"

    def __init__(self, cfg: WebhookConfig, http: httpx.Client):
        self.cfg = cfg
        self.http = http

    def reaches(self, person: Person) -> bool:
        return True

    def has_team(self) -> bool:
        return True

    def payload(self, msg: Message, to: Person | None, about: Person | str | None) -> dict:
        return {
            "key": msg.key,
            "kind": msg.kind,
            "subject": msg.subject,
            "text": plain(msg.text),
            "markdown": msg.text,  # **bold**, for receivers that can show it
            "links": [{"label": link.label, "url": link.url} for link in msg.links],
            "to": to.model_dump(exclude_none=True) if to else None,  # None = the team channel
            "about": who(about) or None,
        }

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        try:
            r = self.http.post(self.cfg.url, headers=self.cfg.headers, json=self.payload(msg, to, about))
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise ChannelError(f"webhook: {e}") from e
