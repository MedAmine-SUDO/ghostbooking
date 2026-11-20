"""Bookings as read from the sheet. All times are naive, in the team's time zone."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass(frozen=True)
class Booking:
    row: int  # row number in the sheet (1-based, as Excel shows it)
    room: str
    start: time
    end: time
    booked_by: str
    title: str = ""
    day: date | None = None  # one-off booking
    weekdays: frozenset[int] | None = None  # recurring booking, 0 = Monday
    protected: bool = False  # "Keep" column says yes
    booked_at: datetime | None = None

    @property
    def recurring(self) -> bool:
        return self.weekdays is not None

    def occurs_on(self, d: date) -> bool:
        if self.recurring:
            return d.weekday() in self.weekdays  # type: ignore[operator]
        return self.day == d

    def span_on(self, d: date) -> tuple[datetime, datetime]:
        return datetime.combine(d, self.start), datetime.combine(d, self.end)

    def when(self) -> str:
        if self.recurring:
            days = sorted(self.weekdays)  # type: ignore[arg-type]
            label = "daily" if len(days) == 7 else "weekdays" if days == [0, 1, 2, 3, 4] else (
                "every " + ", ".join(WEEKDAY_NAMES[d] for d in days))
        else:
            label = self.day.strftime("%a %d %b") if self.day else "?"
        return f"{label} {self.start:%H:%M}–{self.end:%H:%M}"


@dataclass(frozen=True)
class Issue:
    row: int
    message: str


@dataclass
class Sheet:
    """Everything read from the source: parsed bookings plus rows we could not read."""

    bookings: list[Booking] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
