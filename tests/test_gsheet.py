"""Google Sheets source, against a fake gspread client that behaves like the real one."""

from datetime import datetime, time

import pytest
from openpyxl import load_workbook

from ghostbooking.config import Config, SourceConfig, load_config
from ghostbooking.engine import Engine
from ghostbooking.sources import SourceBusy, gsheet, open_source
from ghostbooking.sources.gsheet import spreadsheet_id

URL = "https://docs.google.com/spreadsheets/d/1AbC-dEf_123/edit#gid=0"


class FakeWorksheet:
    def __init__(self, book, title, values, id=0):
        self.spreadsheet, self.title, self.values, self.id = book, title, values, id

    def get_all_values(self):
        width = max(len(r) for r in self.values)
        return [list(r) + [""] * (width - len(r)) for r in self.values]


class FakeBook:
    def __init__(self, tabs: dict[str, list[list[str]]]):
        self.title = "Office"
        self.tabs = [FakeWorksheet(self, t, v, i) for i, (t, v) in enumerate(tabs.items())]
        self.requests: list[dict] = []

    @property
    def sheet1(self):
        return self.tabs[0]

    def worksheets(self):
        return self.tabs

    def worksheet(self, title):
        return next(w for w in self.tabs if w.title == title)

    def batch_update(self, body):
        for req in body["requests"]:
            self.requests.append(req)
            rng = req["deleteDimension"]["range"]
            ws = next(w for w in self.tabs if w.id == rng["sheetId"])
            del ws.values[rng["startIndex"]:rng["endIndex"]]


class FakeClient:
    def __init__(self, book):
        self.book, self.opened = book, []

    def open_by_key(self, key):
        self.opened.append(key)
        return self.book


def as_displayed(v) -> str:
    """What get_all_values returns: the cell as the sheet shows it."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, time):
        return v.strftime("%H:%M")
    return str(v)


@pytest.fixture
def google(example, monkeypatch):
    """The example sheet, uploaded to a fake Google Sheet. Returns (config path, book, client)."""
    ws = load_workbook(example.parent / "room-bookings.xlsx").active
    book = FakeBook({"Notes": [["read me"]],
                     "Bookings": [[as_displayed(c) for c in row] for row in ws.iter_rows(values_only=True)]})
    client = FakeClient(book)
    monkeypatch.setattr(gsheet, "connect", lambda credentials: client)
    text = example.read_text(encoding="utf-8").replace(
        "  type: excel                 # excel | csv\n  path: room-bookings.xlsx",
        f"  type: gsheet\n  spreadsheet: {URL}")
    example.write_text(text, encoding="utf-8")
    return example, book, client


def test_spreadsheet_id_from_url_or_id():
    assert spreadsheet_id(URL) == "1AbC-dEf_123"
    assert spreadsheet_id(" 1AbC-dEf_123 ") == "1AbC-dEf_123"


def test_reads_the_same_bookings_as_the_excel_file(example, google):
    cfg_path, _, client = google
    c = load_config(cfg_path)
    src = open_source(c.source, c.backups)
    sheet = src.read()
    assert src.name == "Office › Bookings"
    assert client.opened == ["1AbC-dEf_123"]
    xlsx = Config(source={"path": example.parent / "room-bookings.xlsx", "sheet": "Bookings",
                          "columns": c.source.columns.model_dump()})
    excel = open_source(xlsx.source, c.backups).read()
    assert [(b.row, b.room, b.day, b.start, b.end) for b in sheet.bookings] == \
           [(b.row, b.room, b.day, b.start, b.end) for b in excel.bookings]
    assert [i.row for i in sheet.issues] == [13]


def test_morning_reset_deletes_rows_bottom_up_with_a_backup(google):
    cfg_path, book, _ = google
    c = load_config(cfg_path)
    p = Engine(c).run_pass(datetime(2026, 11, 23, 7, 30))
    assert sorted(r.booking.row for r in p.plan.removals) == [3, 4, 5]
    assert [r["deleteDimension"]["range"]["startIndex"] for r in book.requests] == [4, 3, 2]
    assert all(r["deleteDimension"]["range"]["sheetId"] == 1 for r in book.requests)  # the Bookings tab
    rooms = [row[0] for row in book.worksheet("Bookings").values[1:]]
    assert rooms == ["Atlas", "Atlas", "Atlas", "Atlas", "Orion", "Kepler", "Kepler", "Orion", "Atlas"]
    assert p.backup.name.startswith("gsheet-Bookings.")
    assert "20/11/2026" in p.backup.read_text()  # the backup has the rows as they were


def test_someone_editing_meanwhile_means_no_delete(google):
    cfg_path, book, _ = google
    c = load_config(cfg_path)
    src = open_source(c.source, c.backups)
    src.read()
    book.worksheet("Bookings").values.insert(1, ["Kepler", "23/11/2026", "16:00", "17:00", "New", "", ""])
    with pytest.raises(SourceBusy, match="changed since it was read"):
        src.remove_rows({3})
    assert book.requests == []


def test_a_release_link_works_on_google_too(google):
    from ghostbooking.config import Links
    from ghostbooking.links import LinkData

    cfg_path, book, _ = google
    c = load_config(cfg_path)
    c.links = Links(base_url="https://x", secret="s" * 20)
    engine = Engine(c)
    engine.run_pass(datetime(2026, 11, 23, 7, 30))
    data = LinkData("release", "Orion", datetime(2026, 11, 23).date(), time(14), time(16), "Sara")
    assert engine.answer(data, datetime(2026, 11, 23, 13, 0))[0] == "Released"
    assert not any(r[0] == "Orion" and r[4] == "Sara" and r[1] == "23/11/2026"
                   for r in book.worksheet("Bookings").values)


def test_missing_tab_lists_the_tabs(google):
    cfg_path, _, _ = google
    c = load_config(cfg_path)
    c.source.sheet = "Rooms"
    with pytest.raises(ValueError, match="tabs: Notes, Bookings"):
        open_source(c.source, c.backups).read()


def test_google_errors_become_retryable(google, monkeypatch):
    cfg_path, book, _ = google

    class APIError(Exception):
        pass

    def boom():
        raise APIError("429 quota exceeded")

    monkeypatch.setattr(book.worksheet("Bookings"), "get_all_values", boom)
    c = load_config(cfg_path)
    with pytest.raises(OSError, match="Google Sheets: 429 quota exceeded"):
        open_source(c.source, c.backups).read()


def test_sharing_hint_when_the_sheet_is_not_shared(monkeypatch):
    class SpreadsheetNotFound(Exception):
        pass

    class Client:
        def open_by_key(self, key):
            raise SpreadsheetNotFound(key)

    monkeypatch.setattr(gsheet, "connect", lambda credentials: Client())
    src = open_source(SourceConfig(type="gsheet", spreadsheet="abc"), "b")
    with pytest.raises(ValueError, match="share it with the service account"):
        src.read()


def test_credentials_are_required(monkeypatch, tmp_path):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(ValueError, match="service-account JSON"):
        gsheet.connect(None)
    with pytest.raises(FileNotFoundError):
        gsheet.connect(tmp_path / "nope.json")


def test_each_source_type_asks_for_what_it_needs():
    with pytest.raises(ValueError, match="needs `spreadsheet`"):
        SourceConfig(type="gsheet")
    with pytest.raises(ValueError, match="needs `path`"):
        SourceConfig(type="csv")
