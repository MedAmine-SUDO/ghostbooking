"""config.yaml schema. Every team's sheet looks different, so columns are mapped here."""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .parsing import parse_recurrence, parse_time


class Columns(BaseModel):
    room: str = "Room"
    date: str = "Date"
    start: str = "Start"
    end: str = "End"
    booked_by: str = "Booked by"
    title: str | None = None
    protected: str | None = None
    booked_at: str | None = None


class SourceConfig(BaseModel):
    type: Literal["excel", "csv"] = "excel"
    path: Path
    sheet: str | None = None  # Excel only; first sheet when empty
    header_row: int = 1
    columns: Columns = Field(default_factory=Columns)
    dayfirst: bool = True  # 03/04 = 3 April; set false for US-style sheets


class ProtectedRule(BaseModel):
    """A booking matching every field set here is never removed."""

    title_contains: str | None = None
    room: str | None = None
    booked_by: str | None = None
    weekdays: str | None = None  # "Mon", "weekdays", "Mon, Thu"
    start: time | None = None

    @field_validator("start", mode="before")
    @classmethod
    def _time(cls, v: object) -> object:
        return None if v is None else parse_time(v)

    @field_validator("weekdays")
    @classmethod
    def _days(cls, v: str | None) -> str | None:
        if v is not None and parse_recurrence(v) is None and parse_recurrence(f"every {v}") is None:
            raise ValueError(f"can't read weekdays {v!r}")
        return v

    def weekday_set(self) -> frozenset[int] | None:
        if self.weekdays is None:
            return None
        return parse_recurrence(self.weekdays) or parse_recurrence(f"every {self.weekdays}")


class Rules(BaseModel):
    reset_at: time = time(7, 30)
    working_days: str = "weekdays"
    day_start: time = time(8, 0)
    day_end: time = time(19, 0)
    conflicts: Literal["notify", "remove"] = "notify"  # what happens to the later booking
    release_ended: bool = True  # free a booking as soon as it ends, not only at the morning reset

    @field_validator("reset_at", "day_start", "day_end", mode="before")
    @classmethod
    def _time(cls, v: object) -> object:
        return parse_time(v)


class Person(BaseModel):
    """How to reach someone. Extra fields (phone, etc.) are passed through to webhooks."""

    model_config = ConfigDict(extra="allow")

    name: str = ""  # filled from the key in `people:`
    aliases: list[str] = Field(default_factory=list)  # initials, nicknames, "Sara B."
    slack: str | None = None  # member ID, e.g. U012AB3CD
    email: str | None = None
    discord: str | None = None  # user ID, for <@mentions>


class ConsoleChannel(BaseModel):
    type: Literal["console"]


class SlackChannel(BaseModel):
    """A bot token sends DMs; an incoming webhook can only post to its channel."""

    type: Literal["slack"]
    token: str | None = None
    webhook_url: str | None = None
    team_channel: str | None = None  # "#rooms" or a channel ID; bot token only

    @model_validator(mode="after")
    def _one_of(self) -> SlackChannel:
        if not self.token and not self.webhook_url:
            raise ValueError("slack needs a bot `token` or a `webhook_url`")
        return self


class EmailChannel(BaseModel):
    type: Literal["email"]
    host: str
    port: int = 587
    username: str | None = None
    password: str | None = None
    security: Literal["starttls", "ssl", "none"] = "starttls"
    sender: str  # From: address
    team_email: str | None = None  # where team messages go, e.g. office@company.com


class TeamsChannel(BaseModel):
    """A Teams Workflows webhook ("post to a channel when a webhook request is received")."""

    type: Literal["teams"]
    webhook_url: str


class DiscordChannel(BaseModel):
    type: Literal["discord"]
    webhook_url: str


class WebhookChannel(BaseModel):
    """JSON POST of every message, for n8n, Make, Zapier or a WhatsApp provider."""

    type: Literal["webhook"]
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


ChannelConfig = Annotated[
    ConsoleChannel | SlackChannel | EmailChannel | TeamsChannel | DiscordChannel | WebhookChannel,
    Field(discriminator="type"),
]


class Reminders(BaseModel):
    before: int = Field(10, ge=0)  # minutes before the start; 0 turns reminders off
    recurring: bool = False  # also remind for "Every Monday" meetings (noisy for daily standups)


class Links(BaseModel):
    """Keep / Release links in reminders. Needs `ghostbooking run`, reachable at `base_url`."""

    base_url: str  # how people reach the endpoint, e.g. https://rooms.example.com
    secret: str = Field(min_length=16)  # signs the links; keep it in an environment variable
    listen: str = "0.0.0.0:8080"
    no_answer: Literal["release", "keep"] = "release"  # nobody tapped Keep after the start
    answer_within: int = Field(15, ge=1)  # minutes after the start

    def address(self) -> tuple[str, int]:
        host, _, port = self.listen.rpartition(":")
        return host or "0.0.0.0", int(port)


class Notifications(BaseModel):
    morning_summary: bool = True  # post what the reset did to the team channel


class Config(BaseModel):
    timezone: str = "UTC"
    source: SourceConfig
    rules: Rules = Field(default_factory=Rules)
    protected: list[ProtectedRule] = Field(default_factory=list)
    backups: Path = Path("backups")
    keep_backups: int = 30
    log: Path = Path("ghostbooking.log.jsonl")
    state: Path = Path("ghostbooking.state.json")  # messages already sent, so nobody is told twice
    people: dict[str, Person] = Field(default_factory=dict)
    channels: list[ChannelConfig] = Field(default_factory=lambda: [ConsoleChannel(type="console")])
    notifications: Notifications = Field(default_factory=Notifications)
    reminders: Reminders = Field(default_factory=Reminders)
    links: Links | None = None  # no links: reminders only, bookings free themselves when they end

    @field_validator("people", mode="before")
    @classmethod
    def _names(cls, v: object) -> object:
        if isinstance(v, dict):
            return {k: {**(p or {}), "name": k} for k, p in v.items()}
        return v


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: object) -> object:
    """Replace ${VAR} and ${VAR:-default} so tokens and passwords stay out of config.yaml."""
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            if m.group(1) in os.environ:
                return os.environ[m.group(1)]
            if m.group(2) is not None:
                return m.group(2)
            raise ValueError(f"config uses ${{{m.group(1)}}} but that environment variable is not set")
        return _ENV.sub(sub, value)
    return value


def load_config(path: str | Path) -> Config:
    path = Path(path)
    data = _expand_env(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    cfg = Config.model_validate(data)
    # Relative paths are relative to the config file, so `-c examples/config.yaml` just works.
    base = path.resolve().parent
    if not cfg.source.path.is_absolute():
        cfg.source.path = base / cfg.source.path
    if not cfg.backups.is_absolute():
        cfg.backups = base / cfg.backups
    if not cfg.log.is_absolute():
        cfg.log = base / cfg.log
    if not cfg.state.is_absolute():
        cfg.state = base / cfg.state
    return cfg
