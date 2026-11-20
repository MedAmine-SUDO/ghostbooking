"""Forgiving parsers for the dates and times people actually type in spreadsheets."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

EXCEL_EPOCH = datetime(1899, 12, 30)

_DAYS = {
    "mon": 0, "monday": 0, "lun": 0, "lundi": 0,
    "tue": 1, "tues": 1, "tuesday": 1, "mar": 1, "mardi": 1,
    "wed": 2, "wednesday": 2, "mer": 2, "mercredi": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "jeu": 3, "jeudi": 3,
    "fri": 4, "friday": 4, "ven": 4, "vendredi": 4,
    "sat": 5, "saturday": 5, "sam": 5, "samedi": 5,
    "sun": 6, "sunday": 6, "dim": 6, "dimanche": 6,
}
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d %b %Y", "%d %B %Y")
_DATE_FORMATS_MDY = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%b %d %Y", "%B %d %Y")


class ParseError(ValueError):
    pass


def _norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def parse_recurrence(value: object) -> frozenset[int] | None:
    """'Every Monday', 'daily', 'weekdays', 'Mon, Wed', 'tous les lundis' -> weekday set, else None."""
    if not isinstance(value, str):
        return None
    text = _norm(value)
    if text in {"daily", "every day", "everyday", "tous les jours", "chaque jour"}:
        return frozenset(range(7))
    if text in {"weekdays", "every weekday", "mon-fri", "monday-friday", "lun-ven", "en semaine"}:
        return frozenset(range(5))
    text = re.sub(r"^(every|each|tous les|toutes les|chaque)\s+", "", text)
    parts = [p.strip() for p in re.split(r"[,/&]|\band\b|\bet\b", text) if p.strip()]
    days = set()
    for p in parts:
        if p in _DAYS:
            days.add(_DAYS[p])
        elif p.endswith("s") and p[:-1] in _DAYS:  # "mondays", "lundis"
            days.add(_DAYS[p[:-1]])
        else:
            return None
    return frozenset(days) if days else None


def parse_date(value: object, dayfirst: bool = True) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 20000 < value < 80000:  # Excel serial date (1954..2119)
            return (EXCEL_EPOCH + timedelta(days=float(value))).date()
        raise ParseError(f"not a date: {value!r}")
    if isinstance(value, str) and value.strip():
        text = re.sub(r"\s+", " ", value.strip())
        text = re.sub(r"^(mon|tue|wed|thu|fri|sat|sun|lun|mar|mer|jeu|ven|sam|dim)\w*\.?,?\s+", "",
                      text, flags=re.IGNORECASE)
        for fmt in _DATE_FORMATS if dayfirst else _DATE_FORMATS_MDY:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise ParseError(f"not a date: {value!r}")


_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})(?:\s*(?:[:h.]\s*)(?P<m>\d{2})?)?\s*(?P<ampm>am|pm|a\.m\.|p\.m\.)?$"
)


def parse_time(value: object) -> time:
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if 0 <= value < 1:  # Excel stores times as a fraction of a day
            minutes = round(float(value) * 24 * 60)
            return time(minutes // 60 % 24, minutes % 60)
        if isinstance(value, int) and 0 <= value <= 23:
            return time(value, 0)
        raise ParseError(f"not a time: {value!r}")
    if isinstance(value, str):
        text = _norm(value).replace(" ", "")
        if re.fullmatch(r"\d{3,4}", text):  # 1400, 930
            text = text[:-2] + ":" + text[-2:]
        m = _TIME_RE.match(text)
        if m:
            h, mnt = int(m["h"]), int(m["m"] or 0)
            ampm = (m["ampm"] or "").replace(".", "")
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            if 0 <= h <= 23 and 0 <= mnt <= 59:
                return time(h, mnt)
    raise ParseError(f"not a time: {value!r}")


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return _norm(value) in {"yes", "y", "true", "1", "x", "oui", "o", "keep", "✓", "✔"}
