from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import load_workbook

from .base import FileSource


class ExcelSource(FileSource):
    """An .xlsx file on a shared drive or synced folder. Only the booking table's values move;
    formatting, other sheets and column widths stay as they are."""

    def _sheet(self, wb):
        if self.cfg.sheet:
            if self.cfg.sheet not in wb.sheetnames:
                raise ValueError(f"sheet {self.cfg.sheet!r} not found; sheets: {', '.join(wb.sheetnames)}")
            return wb[self.cfg.sheet]
        return wb.worksheets[0]

    def _rows(self):
        wb = load_workbook(self.path, data_only=True)
        ws = self._sheet(wb)
        h = self.cfg.header_row
        header = [c.value for c in ws[h]]
        rows = [(r, [c.value for c in ws[r]]) for r in range(h + 1, ws.max_row + 1)]
        wb.close()
        return header, rows

    def _write_keeping(self, keep_rows: set[int], tmp: Path) -> None:
        wb = load_workbook(self.path)
        ws = self._sheet(wb)
        h, width = self.cfg.header_row, ws.max_column
        first, last = h + 1, ws.max_row
        # A booking's formatting (date format, colour coding) moves with it. Rows freed at the
        # bottom take the look of the first row below the table, which is how the sheet's
        # empty rows already look.
        kept = [[(ws.cell(r, c).value, copy(ws.cell(r, c)._style)) for c in range(1, width + 1)]
                for r in range(first, last + 1) if r in keep_rows]
        blank = [(None, copy(ws.cell(last + 1, c)._style)) for c in range(1, width + 1)]
        for offset, r in enumerate(range(first, last + 1)):
            cells = kept[offset] if offset < len(kept) else blank
            for c, (value, style) in enumerate(cells, start=1):
                cell = ws.cell(r, c)
                cell.value = value
                cell._style = copy(style)
        wb.save(tmp)
        wb.close()
