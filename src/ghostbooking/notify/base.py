from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import Person
from .message import Message


class ChannelError(RuntimeError):
    """A message could not be delivered. The run carries on; the failure is logged."""


class Channel(ABC):
    name: str = "channel"
    mirror = False  # shows every message, but doesn't count as delivering it (console)

    def reaches(self, person: Person) -> bool:
        """Can this channel message the person directly?"""
        return False

    def has_team(self) -> bool:
        """Can this channel post to a shared team channel?"""
        return False

    @abstractmethod
    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        """Send to `to` directly, or to the team channel when `to` is None.

        `about` is who a team post is for (a person we couldn't reach, or a name we
        couldn't match), so the channel can mention them.
        """


def who(about: Person | str | None) -> str:
    if about is None:
        return ""
    return about if isinstance(about, str) else about.name
