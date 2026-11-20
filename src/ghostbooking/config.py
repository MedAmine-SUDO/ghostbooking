"""config.yaml schema. Every team's sheet looks different, so columns are mapped here."""

from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

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

    @field_validator("reset_at", "day_start", "day_end", mode="before")
    @classmethod
    def _time(cls, v: object) -> object:
        return parse_time(v)


class Config(BaseModel):
    timezone: str = "UTC"
    source: SourceConfig
    rules: Rules = Field(default_factory=Rules)
    protected: list[ProtectedRule] = Field(default_factory=list)
    backups: Path = Path("backups")
    keep_backups: int = 30
    log: Path = Path("ghostbooking.log.jsonl")


def load_config(path: str | Path) -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config.model_validate(data)
    # Relative paths are relative to the config file, so `-c examples/config.yaml` just works.
    base = path.resolve().parent
    if not cfg.source.path.is_absolute():
        cfg.source.path = base / cfg.source.path
    if not cfg.backups.is_absolute():
        cfg.backups = base / cfg.backups
    if not cfg.log.is_absolute():
        cfg.log = base / cfg.log
    return cfg
