"""A Google Sheet, read and edited through a service account the sheet is shared with.

People edit a Google Sheet live, so there's no "file is open" to wait for. Instead, right before
deleting, the rows are fetched again: if any row to delete no longer holds what was read (someone
inserted, sorted or edited), nothing is deleted and the next pass tries again.
"""

from __future__ import annotations

import csv
import os
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..config import SourceConfig
from .base import Source, SourceBusy

_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def spreadsheet_id(value: str) -> str:
    m = _ID.search(value)
    return m.group(1) if m else value.strip()


@contextmanager
def _google_errors():
    """Quota limits, outages and lost access become OSError: the scheduler retries next pass."""
    try:
        yield
    except (ValueError, OSError, SourceBusy):
        raise
    except Exception as e:  # gspread and google-auth raise many kinds
        if type(e).__name__ == "SpreadsheetNotFound":
            raise ValueError("spreadsheet not found: share it with the service account's email "
                             "(client_email in the JSON file) as an Editor") from e
        raise OSError(f"Google Sheets: {e}") from e


def connect(credentials: Path | None):
    try:
        import gspread
    except ImportError as e:
        raise ValueError("Google Sheets needs the google extra: pip install 'ghostbooking[google]'") from e
    path = credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not path:
        raise ValueError("set source.credentials (or GOOGLE_APPLICATION_CREDENTIALS) to the "
                         "service-account JSON file")
    if not Path(path).exists():
        raise FileNotFoundError(f"service-account file not found: {path}")
    return gspread.service_account(filename=str(path))


class GoogleSheetSource(Source):
    def __init__(self, cfg: SourceConfig, backups_dir: Path, keep_backups: int = 30, client=None):
        super().__init__(cfg, backups_dir, keep_backups)
        self._client = client
        self._ws = None
        self._read: dict[int, list[str]] | None = None

    def _worksheet(self):
        with _google_errors():
            return self._open()

    def _open(self):
        if self._ws is None:
            client = self._client or connect(self.cfg.credentials)
            book = client.open_by_key(spreadsheet_id(self.cfg.spreadsheet or ""))
            if self.cfg.sheet:
                titles = [w.title for w in book.worksheets()]
                if self.cfg.sheet not in titles:
                    raise ValueError(f"tab {self.cfg.sheet!r} not found; tabs: {', '.join(titles)}")
                self._ws = book.worksheet(self.cfg.sheet)
            else:
                self._ws = book.sheet1
        return self._ws

    @property
    def name(self) -> str:
        ws = self._worksheet()
        return f"{ws.spreadsheet.title} › {ws.title}"

    def _values(self) -> list[list[str]]:
        with _google_errors():
            return self._worksheet().get_all_values()  # as displayed in the sheet

    def _rows(self):
        data = self._values()
        h = self.cfg.header_row
        header = data[h - 1] if len(data) >= h else []
        rows = [(i, [v if v != "" else None for v in values])
                for i, values in enumerate(data[h:], start=h + 1)]
        self._read = {i: values for i, values in enumerate(data[h:], start=h + 1)}
        return header, rows

    def remove_rows(self, rows: set[int]) -> Path | None:
        if not rows:
            return None
        if self._read is None:
            raise SourceBusy("read the sheet before removing rows")
        data = self._values()
        now = {i: values for i, values in enumerate(data, start=1)}
        if any(now.get(r) != self._read.get(r) for r in rows):
            raise SourceBusy(f"{self.name} changed since it was read; trying again next time")
        backup = self._backup(data)
        ws = self._worksheet()
        # Bottom-up in one request, so the row numbers above stay right while deleting.
        with _google_errors():
            ws.spreadsheet.batch_update({"requests": [
                {"deleteDimension": {"range": {"sheetId": ws.id, "dimension": "ROWS",
                                               "startIndex": r - 1, "endIndex": r}}}
                for r in sorted(rows, reverse=True)
            ]})
        self._read = None
        return backup

    def _backup(self, data: list[list[str]]) -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^A-Za-z0-9]+", "-", self._worksheet().title).strip("-") or "sheet"
        dest = self.backups_dir / f"gsheet-{slug}.{datetime.now():%Y%m%d-%H%M%S}.csv"
        with dest.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(data)
        self._prune_backups(f"gsheet-{slug}.*.csv")
        return dest
