from datetime import date, datetime, time

import pytest

from ghostbooking.parsing import ParseError, parse_bool, parse_date, parse_recurrence, parse_time


@pytest.mark.parametrize("value, expected", [
    ("20/11/2026", date(2026, 11, 20)),
    ("2026-11-20", date(2026, 11, 20)),
    ("Fri 20/11/2026", date(2026, 11, 20)),
    ("vendredi 20/11/2026", date(2026, 11, 20)),
    ("20.11.2026", date(2026, 11, 20)),
    (datetime(2026, 11, 20, 9, 0), date(2026, 11, 20)),
    (46346, date(2026, 11, 20)),  # Excel serial date
])
def test_dates(value, expected):
    assert parse_date(value) == expected


def test_us_dates_when_configured():
    assert parse_date("11/20/2026", dayfirst=False) == date(2026, 11, 20)


@pytest.mark.parametrize("value, expected", [
    ("14:00", time(14, 0)), ("14h", time(14, 0)), ("14h30", time(14, 30)), ("14.30", time(14, 30)),
    ("2pm", time(14, 0)), ("2:30 pm", time(14, 30)), ("12am", time(0, 0)), ("1400", time(14, 0)),
    ("930", time(9, 30)), ("9", time(9, 0)), (9, time(9, 0)), (0.5, time(12, 0)),
    (time(10, 15, 59), time(10, 15)), (datetime(2026, 1, 1, 8, 5), time(8, 5)),
])
def test_times(value, expected):
    assert parse_time(value) == expected


@pytest.mark.parametrize("bad", ["tbd", "25:00", "", None, "14:75"])
def test_bad_times(bad):
    with pytest.raises(ParseError):
        parse_time(bad)


@pytest.mark.parametrize("value, expected", [
    ("Every Monday", {0}), ("daily", set(range(7))), ("weekdays", set(range(5))),
    ("Mon, Wed", {0, 2}), ("tous les lundis", {0}), ("Mondays", {0}), ("every tue & thu", {1, 3}),
])
def test_recurrence(value, expected):
    assert parse_recurrence(value) == frozenset(expected)


@pytest.mark.parametrize("value", ["20/11/2026", "next week", "", None, 46346])
def test_not_recurrence(value):
    assert parse_recurrence(value) is None


def test_bool():
    assert parse_bool("Yes") and parse_bool("oui") and parse_bool("x") and parse_bool(True)
    assert not parse_bool(None) and not parse_bool("no") and not parse_bool("")
