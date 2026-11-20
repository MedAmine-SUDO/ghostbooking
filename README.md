# 👻 Ghostbooking

**For startups that still book meeting rooms in a shared Excel file.**

Every morning, Ghostbooking clears the bookings people forgot to remove, flags double bookings with a free alternative, and (soon) reminds people before and after their meetings on Slack, Teams, Discord, email or any webhook. Nobody has to change how they book.

> Work in progress: steps 1–3 of [SPEC.md](SPEC.md) are done (reading the sheet, the cleanup rules, safe writes). Notifications and the scheduler come next.

## Try it in 30 seconds

```bash
uv sync
uv run ghostbooking check -c examples/config.yaml --now "2026-11-23 07:30"
uv run ghostbooking tick  -c examples/config.yaml --now "2026-11-23 07:30" --dry-run
```

The example sheet has ghost bookings left over from Friday, a double booking, a recurring standup, a protected board meeting and a row with "tbd" as its end time.

## Safe on a shared file

- A backup before every write (the last 30 are kept).
- Never writes while someone has the file open in Excel or LibreOffice.
- Re-checks the file right before writing; if it changed, it does nothing.
- Only the booking rows move. Formatting, other sheets, column widths and frozen headers stay.
- Rows it can't read are reported and left alone.
- `--dry-run` shows what would change without writing anything.
