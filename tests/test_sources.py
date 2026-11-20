import os
from datetime import datetime

import pytest
from openpyxl import load_workbook

from ghostbooking.config import load_config
from ghostbooking.rules import plan_reset
from ghostbooking.sources import SourceBusy, open_source

NOW = datetime(2026, 11, 23, 7, 30)


def _source(config_path):
    cfg = load_config(config_path)
    return cfg, open_source(cfg.source, cfg.backups, cfg.keep_backups)


def test_reads_the_example(example):
    _, src = _source(example)
    sheet = src.read()
    assert len(sheet.bookings) == 11
    assert [(i.row, i.message) for i in sheet.issues] == [(13, "not a time: 'tbd'")]


def test_reset_removes_ghosts_and_keeps_the_workbook_intact(example):
    cfg, src = _source(example)
    plan = plan_reset(src.read(), cfg, NOW)
    backup = src.remove_rows(plan.rows_to_remove)
    assert backup.exists()

    wb = load_workbook(cfg.source.path)
    ws = wb["Bookings"]
    rooms = [r[0] for r in ws.iter_rows(min_row=2, values_only=True) if r[0]]
    assert len(rooms) == 9  # 12 rows - 3 ghosts
    assert wb.sheetnames == ["Bookings", "Rooms"]
    assert ws["A1"].fill.fgColor.rgb.endswith("2456C8") and ws.freeze_panes == "A2"
    # Youssef's real Excel date moved up from row 6 to row 3 and kept its date format.
    assert ws["E3"].value == "Youssef" and ws["B3"].number_format == "dd/mm/yyyy"
    assert all(c.value is None for c in ws[11])  # freed rows at the bottom are empty


def test_never_writes_while_the_file_is_open_in_excel(example):
    cfg, src = _source(example)
    src.read()
    lock = cfg.source.path.with_name("~$" + cfg.source.path.name)
    lock.touch()
    before = cfg.source.path.read_bytes()
    with pytest.raises(SourceBusy):
        src.remove_rows({3})
    assert cfg.source.path.read_bytes() == before


def test_refuses_to_write_if_the_file_changed_since_it_was_read(example):
    cfg, src = _source(example)
    src.read()
    st = cfg.source.path.stat()
    os.utime(cfg.source.path, (st.st_atime, st.st_mtime + 5))
    with pytest.raises(SourceBusy):
        src.remove_rows({3})


def test_keeps_only_the_newest_backups(example):
    cfg, src = _source(example)
    src.keep_backups = 2
    for i in range(4):
        cfg.backups.mkdir(exist_ok=True)
        (cfg.backups / f"room-bookings.2026010{i}-000000.xlsx").write_bytes(b"x")
    src.read()
    src.remove_rows({3})
    assert len(list(cfg.backups.glob("*.xlsx"))) == 2


def test_csv_source(tmp_path):
    (tmp_path / "b.csv").write_text(
        "Room;Date;Start;End;Booked by\nAtlas;20/11/2026;14h;16h;Sara\nOrion;23/11/2026;10:00;11:00;Ali\n",
        encoding="utf-8")
    (tmp_path / "config.yaml").write_text("source:\n  type: csv\n  path: b.csv\n", encoding="utf-8")
    cfg, src = _source(tmp_path / "config.yaml")
    plan = plan_reset(src.read(), cfg, NOW)
    src.remove_rows(plan.rows_to_remove)
    assert (tmp_path / "b.csv").read_text().splitlines()[1:] == ["Orion,23/11/2026,10:00,11:00,Ali"]


def test_wrong_column_name_says_what_it_found(tmp_path):
    (tmp_path / "b.csv").write_text("Salle,Jour,Début,Fin,Par\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text("source:\n  type: csv\n  path: b.csv\n", encoding="utf-8")
    _, src = _source(tmp_path / "config.yaml")
    with pytest.raises(ValueError, match="Found: Salle, Jour"):
        src.read()
