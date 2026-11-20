"""Build examples/room-bookings.xlsx: a realistic, slightly messy booking sheet.

Pretend it's Monday 23 November 2026, 07:30, before anyone arrives.
"""

from datetime import datetime, time
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

ROWS = [
    # Room, Date, Start, End, Booked by, Meeting, Keep
    ("Atlas", "Every Monday", "09:30", "09:45", "Lina", "Weekly standup", None),
    ("Orion", "20/11/2026", "10:00", "11:00", "Karim", "Client demo", None),          # Friday, forgotten
    ("Atlas", datetime(2026, 11, 20), "14h", "16h", "Sara", "Sprint review", None),   # Friday, forgotten
    ("Kepler", "Fri 20/11/2026", "2pm", "3pm", "Omar", "Interview", None),            # Friday, forgotten
    ("Atlas", datetime(2026, 11, 23), time(10, 0), time(11, 0), "Youssef", "Roadmap", None),
    ("Atlas", "23/11/2026", "14:00", "15:00", "Ines", "Design review", None),
    ("Atlas", "23/11/2026", "14:30", "15:30", "Karim", "Partner call", None),          # double-booked
    ("Orion", "23/11/2026", "14:00", "16:00", "Sara", "Hiring sync", None),
    ("Kepler", "24/11/2026", "9", "10", "Lina", "1:1", None),
    ("Kepler", "Every Friday", "17:00", "18:00", "Everyone", "Demo day", None),
    ("Orion", "19/11/2026", "18:00", "20:00", "Youssef", "Board meeting", "yes"),       # protected
    ("Atlas", "23/11/2026", "16:00", "tbd", "Omar", "Investor call", None),            # unreadable
]


def build(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bookings"
    ws.append(["Room", "Date", "Start", "End", "Booked by", "Meeting", "Keep"])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="2456C8")
        cell.alignment = Alignment(horizontal="center")
    for row in ROWS:
        ws.append(list(row))
    for r in range(2, ws.max_row + 1):
        if isinstance(ws.cell(r, 2).value, datetime):
            ws.cell(r, 2).number_format = "dd/mm/yyyy"
        for c in (3, 4):
            if isinstance(ws.cell(r, c).value, time):
                ws.cell(r, c).number_format = "hh:mm"
    for col, width in zip("ABCDEFG", (10, 16, 9, 9, 12, 18, 7)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.create_sheet("Rooms").append(["Atlas: 8 seats, screen", "Orion: 4 seats", "Kepler: 6 seats"])
    wb.save(path)


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "examples" / "room-bookings.xlsx"
    build(out)
    print(f"wrote {out}")
