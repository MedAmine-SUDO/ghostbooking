from __future__ import annotations

import csv
from pathlib import Path

from .base import Source


class CsvSource(Source):
    def _read_all(self) -> list[list[str]]:
        with self.path.open(newline="", encoding="utf-8-sig") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            return [row for row in csv.reader(f, dialect)]

    def _rows(self):
        data = self._read_all()
        h = self.cfg.header_row
        header = data[h - 1] if len(data) >= h else []
        rows = [(i, [v if v != "" else None for v in values])
                for i, values in enumerate(data[h:], start=h + 1)]
        return header, rows

    def _write_keeping(self, keep_rows: set[int], tmp: Path) -> None:
        data = self._read_all()
        h = self.cfg.header_row
        out = data[:h] + [values for i, values in enumerate(data[h:], start=h + 1) if i in keep_rows]
        with tmp.open("w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(out)
