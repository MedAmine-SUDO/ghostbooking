from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from ..config import SourceConfig
from ..models import Booking, Issue, Sheet
from ..parsing import ParseError, parse_bool, parse_date, parse_recurrence, parse_time


class SourceBusy(RuntimeError):
    """Someone has the file open, or it changed while we were working. Try again later."""


class Source(ABC):
    """Reads booking rows from somewhere and removes rows safely. Parsing is shared."""

    def __init__(self, cfg: SourceConfig, backups_dir: Path, keep_backups: int = 30):
        self.cfg = cfg
        self.backups_dir = Path(backups_dir)
        self.keep_backups = keep_backups

    @property
    @abstractmethod
    def name(self) -> str:
        """What people call it in messages: the file name or the sheet's title."""

    def is_open_elsewhere(self) -> bool:
        return False

    @abstractmethod
    def remove_rows(self, rows: set[int]) -> Path | None:
        """Remove the given sheet rows safely. Returns the backup path, or None if nothing to do.
        Raises SourceBusy when it isn't safe right now."""

    # --- reading -------------------------------------------------------------------------
    @abstractmethod
    def _rows(self) -> tuple[list[object], list[tuple[int, list[object]]]]:
        """Return (header values, [(sheet row number, values)])."""

    def read(self) -> Sheet:
        header, rows = self._rows()
        index = self._column_index(header)
        sheet = Sheet()
        for rownum, values in rows:
            if all(v is None or str(v).strip() == "" for v in values):
                continue
            try:
                sheet.bookings.append(self._booking(rownum, values, index))
            except ParseError as e:
                sheet.issues.append(Issue(rownum, str(e)))
        return sheet

    def _column_index(self, header: list[object]) -> dict[str, int]:
        names = {str(h).strip().lower(): i for i, h in enumerate(header) if h is not None}
        index: dict[str, int] = {}
        for field, wanted in self.cfg.columns.model_dump().items():
            if wanted is None:
                continue
            i = names.get(wanted.strip().lower())
            if i is None:
                found = ", ".join(str(h) for h in header if h is not None) or "nothing"
                raise ValueError(
                    f"column {wanted!r} (for {field}) not found in row {self.cfg.header_row}. "
                    f"Found: {found}. Fix `source.columns` in config.yaml."
                )
            index[field] = i
        return index

    def _booking(self, rownum: int, values: list[object], index: dict[str, int]) -> Booking:
        def get(field: str) -> object:
            i = index.get(field)
            return values[i] if i is not None and i < len(values) else None

        room = str(get("room") or "").strip()
        if not room:
            raise ParseError("no room")
        raw_date = get("date")
        weekdays = parse_recurrence(raw_date)
        day = None if weekdays is not None else parse_date(raw_date, self.cfg.dayfirst)
        start, end = parse_time(get("start")), parse_time(get("end"))
        if end <= start:
            raise ParseError(f"ends before it starts ({start:%H:%M}–{end:%H:%M})")
        booked_at = None
        if "booked_at" in index and get("booked_at") not in (None, ""):
            v = get("booked_at")
            booked_at = v if isinstance(v, datetime) else datetime.combine(parse_date(v), datetime.min.time())
        return Booking(
            row=rownum,
            room=room,
            start=start,
            end=end,
            booked_by=str(get("booked_by") or "").strip(),
            title=str(get("title") or "").strip(),
            day=day,
            weekdays=weekdays,
            protected=parse_bool(get("protected")),
            booked_at=booked_at,
        )

    def _prune_backups(self, pattern: str) -> None:
        olds = sorted(self.backups_dir.glob(pattern))
        for old in olds[: max(0, len(olds) - self.keep_backups)]:
            old.unlink()


class FileSource(Source):
    """An Excel or CSV file on a shared drive. Never writes while someone has it open."""

    def __init__(self, cfg: SourceConfig, backups_dir: Path, keep_backups: int = 30):
        super().__init__(cfg, backups_dir, keep_backups)
        if cfg.path is None:
            raise ValueError("source.path is required for excel and csv sources")
        self.path = Path(cfg.path)
        self._read_mtime: float | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def read(self) -> Sheet:
        if not self.path.exists():
            raise FileNotFoundError(f"booking file not found: {self.path}")
        self._read_mtime = self.path.stat().st_mtime
        return super().read()

    # --- writing -------------------------------------------------------------------------
    @abstractmethod
    def _write_keeping(self, keep_rows: set[int], tmp: Path) -> None:
        """Write the file to `tmp`, keeping only `keep_rows` in the booking table (compacted)."""

    def lock_file(self) -> Path:
        # Excel and LibreOffice drop a lock file next to a workbook while it is open.
        return self.path.with_name("~$" + self.path.name)

    def is_open_elsewhere(self) -> bool:
        lo_lock = self.path.with_name(f".~lock.{self.path.name}#")
        return self.lock_file().exists() or lo_lock.exists()

    def remove_rows(self, rows: set[int]) -> Path | None:
        if not rows:
            return None
        if self.is_open_elsewhere():
            raise SourceBusy(f"{self.path.name} is open in Excel right now; not writing over it")
        if self._read_mtime is not None and self.path.stat().st_mtime != self._read_mtime:
            raise SourceBusy(f"{self.path.name} changed since it was read; read it again first")
        backup = self._backup()
        _, all_rows = self._rows()
        keep = {r for r, values in all_rows if r not in rows}
        tmp = self.path.with_name(f".{self.path.stem}.ghostbooking-tmp{self.path.suffix}")
        try:
            self._write_keeping(keep, tmp)
            if self.is_open_elsewhere():  # opened while we were writing
                raise SourceBusy(f"{self.path.name} was opened while writing; nothing changed")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self._read_mtime = self.path.stat().st_mtime
        return backup

    def _backup(self) -> Path:
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.backups_dir / f"{self.path.stem}.{stamp}{self.path.suffix}"
        shutil.copy2(self.path, dest)
        self._prune_backups(f"{self.path.stem}.*{self.path.suffix}")
        return dest
