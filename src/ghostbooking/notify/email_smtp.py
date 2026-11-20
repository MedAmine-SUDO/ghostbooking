from __future__ import annotations

import smtplib
from collections.abc import Callable
from email.message import EmailMessage

from ..config import EmailChannel as EmailConfig
from ..config import Person
from .base import Channel, ChannelError, who
from .message import Message, plain


class EmailChannel(Channel):
    """SMTP. Writes to people with an `email`, and to `team_email` for team messages."""

    name = "email"

    def __init__(self, cfg: EmailConfig, smtp: Callable[..., smtplib.SMTP] | None = None):
        self.cfg = cfg
        self.smtp = smtp or (smtplib.SMTP_SSL if cfg.security == "ssl" else smtplib.SMTP)

    def reaches(self, person: Person) -> bool:
        return bool(person.email)

    def has_team(self) -> bool:
        return bool(self.cfg.team_email)

    def build(self, msg: Message, to: Person | None, about: Person | str | None) -> EmailMessage:
        em = EmailMessage()
        em["From"] = self.cfg.sender
        em["To"] = to.email if to else self.cfg.team_email
        em["Subject"] = msg.subject + (f" (for {who(about)})" if about and not to else "")
        body = plain(msg.text)
        if msg.links:
            body += "\n\n" + "\n".join(f"{link.label}: {link.url}" for link in msg.links)
        em.set_content(body + "\n\n-- \nSent by Ghostbooking\n")
        return em

    def send(self, msg: Message, to: Person | None, about: Person | str | None) -> None:
        try:
            with self.smtp(self.cfg.host, self.cfg.port, timeout=20) as s:
                if self.cfg.security == "starttls":
                    s.starttls()
                if self.cfg.username:
                    s.login(self.cfg.username, self.cfg.password or "")
                s.send_message(self.build(msg, to, about))
        except (smtplib.SMTPException, OSError) as e:
            raise ChannelError(f"email: {e}") from e
