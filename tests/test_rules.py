from datetime import date, datetime, time
from pathlib import Path

from ghostbooking.config import Config, ProtectedRule
from ghostbooking.models import Booking, Sheet
from ghostbooking.rules import plan_reset

NOW = datetime(2026, 11, 23, 7, 30)  # Monday morning
MON, FRI = date(2026, 11, 23), date(2026, 11, 20)


def cfg(**kw) -> Config:
    return Config(source={"path": Path("x.xlsx")}, **kw)


def b(row, room, day, start, end, who="A", title="", **kw) -> Booking:
    return Booking(row=row, room=room, day=day, start=time(*start), end=time(*end),
                   booked_by=who, title=title, **kw)


def test_leftovers_from_past_days_are_ghosts():
    sheet = Sheet([b(2, "Atlas", FRI, (14,), (16,)), b(3, "Atlas", MON, (10,), (11,))])
    plan = plan_reset(sheet, cfg(), NOW)
    assert plan.rows_to_remove == {2}
    assert "left over from Fri 20 Nov" in plan.removals[0].reason


def test_meeting_that_ended_earlier_today():
    plan = plan_reset(Sheet([b(2, "Atlas", MON, (8,), (9,))]), cfg(), datetime(2026, 11, 23, 9, 30))
    assert plan.removals[0].reason == "ended at 09:00 today"


def test_recurring_and_flagged_and_rule_protected_are_kept():
    sheet = Sheet([
        Booking(row=2, room="Atlas", start=time(9, 30), end=time(9, 45), booked_by="L",
                weekdays=frozenset({0}), title="Standup"),
        b(3, "Orion", FRI, (18,), (20,), protected=True),
        b(4, "Kepler", FRI, (17,), (18,), title="Board meeting"),
    ])
    plan = plan_reset(sheet, cfg(protected=[ProtectedRule(title_contains="board")]), NOW)
    assert plan.removals == []
    assert {x.row for x in plan.protected} == {2, 3, 4}


def test_rule_needs_all_its_fields_to_match():
    rule = ProtectedRule(room="Atlas", weekdays="Fri")
    sheet = Sheet([b(2, "Atlas", FRI, (9,), (10,)), b(3, "Orion", FRI, (9,), (10,))])
    assert plan_reset(sheet, cfg(protected=[rule]), NOW).rows_to_remove == {3}


def test_double_booking_first_row_wins_and_other_room_is_suggested():
    sheet = Sheet([
        b(2, "Atlas", MON, (14,), (15,), "Ines"),
        b(3, "Atlas", MON, (14, 30), (15, 30), "Karim"),
        b(4, "Orion", MON, (14,), (16,)),
        b(5, "Kepler", MON, (9,), (10,)),
    ])
    plan = plan_reset(sheet, cfg(), NOW)
    [c] = plan.conflicts
    assert (c.kept.row, c.clashing.row) == (2, 3)
    assert c.suggestion == "Kepler is free 14:30–15:30"
    assert plan.removals == []  # default: notify only


def test_same_room_later_when_no_other_room_is_free():
    sheet = Sheet([b(2, "Atlas", MON, (14,), (15,)), b(3, "Atlas", MON, (14, 30), (15, 30))])
    [c] = plan_reset(sheet, cfg(), NOW).conflicts
    assert c.suggestion == "Atlas is free 15:00–16:00"


def test_booked_at_beats_row_order_and_protected_beats_both():
    sheet = Sheet([
        b(2, "Atlas", MON, (14,), (15,), booked_at=datetime(2026, 11, 22, 18)),
        b(3, "Atlas", MON, (14,), (15,), booked_at=datetime(2026, 11, 21, 9)),
    ])
    assert plan_reset(sheet, cfg(), NOW).conflicts[0].kept.row == 3
    standup = Booking(row=4, room="Atlas", start=time(14), end=time(14, 15), booked_by="T",
                      weekdays=frozenset({0}))
    plan = plan_reset(Sheet([b(2, "Atlas", MON, (14,), (15,)), standup]), cfg(), NOW)
    assert plan.conflicts[0].kept.row == 4


def test_remove_mode_drops_the_later_booking():
    sheet = Sheet([b(2, "Atlas", MON, (14,), (15,)), b(3, "Atlas", MON, (14,), (15,))])
    plan = plan_reset(sheet, cfg(rules={"conflicts": "remove"}), NOW)
    assert plan.rows_to_remove == {3}


def test_back_to_back_is_not_a_conflict():
    sheet = Sheet([b(2, "Atlas", MON, (14,), (15,)), b(3, "Atlas", MON, (15,), (16,))])
    assert plan_reset(sheet, cfg(), NOW).conflicts == []
