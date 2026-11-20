"""Where bookings live. Each source reads rows into bookings and writes back the rows to keep."""

from __future__ import annotations

from ..config import SourceConfig
from .base import Source, SourceBusy
from .csv_file import CsvSource
from .excel import ExcelSource


def open_source(cfg: SourceConfig, backups_dir, keep_backups: int = 30) -> Source:
    cls = {"excel": ExcelSource, "csv": CsvSource}[cfg.type]
    return cls(cfg, backups_dir, keep_backups)


__all__ = ["Source", "SourceBusy", "open_source"]
