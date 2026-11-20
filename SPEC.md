# Ghostbooking: spec v1 (20 Nov 2026)

> **For startups that still book meeting rooms in a shared Excel file.**
> Ghostbooking clears forgotten bookings every morning, fixes double bookings, and reminds people before and after their meetings on whatever chat tool the team already uses. Nobody has to change how they book.

Clean-room: built from scratch, fully generic. Nothing from the client job (code, file layout, rules, names) goes in. The client story lives only in the case study.

## 1. Who it's for

- A team of roughly 10–100 people, with 2–10 meeting rooms.
- Rooms are booked in **one shared spreadsheet**: an Excel file on a shared drive or synced folder (OneDrive, Dropbox, Google Drive desktop, a network share), or a Google Sheet.
- No paid room-booking tool, and no plan to buy one.
- The pain: bookings left in the file after the meeting ("ghost bookings"), double bookings, and chasing colleagues to ask whether the room is really taken.

Not for: teams already on Google Calendar or Outlook room resources (they have auto-release built in), or on Skedda, Robin and similar tools.

## 2. What it does

| Job | When | What happens |
|---|---|---|
| **Morning reset** | Every working day, e.g. 07:30 | Removes past bookings and expired ones, keeps protected recurring meetings, writes a fresh, clean file. |
| **Conflict fixing** | At the reset and on every scan | Finds overlapping bookings for the same room. Rule: first booked wins (by row order or a "booked at" column); the other person is told and offered the next free slot. |
| **Reminder** | N minutes before start (default 10) | "Your meeting in Atlas starts at 14:00." |
| **Keep or release?** | At the booked end time | Message with two one-click links: **Keep** (extends nothing, just confirms) / **Release** (frees the rest of the slot). |
| **Auto-release** | If no answer within N minutes (default 15) | Applies the configured default: release (default) or keep. |
| **Weekly summary** | Friday afternoon | To the team channel: ghost bookings cleared, room-hours freed, conflicts fixed, busiest room. |

**Protected meetings:** rules in config, e.g. `title contains "Standup"`, `room: Atlas, weekday: Mon, 10:00–11:00`, or a "Protected" column set to yes. These are never removed.

## 3. Sources (v1)

- **Excel (.xlsx) file** at a path the service can reach (synced folder or network share).
- **CSV** (same rules, for teams exporting from something else).
- **Google Sheets** (service account, sheet shared with it).

Later: Excel on OneDrive/SharePoint via Microsoft Graph (no synced folder needed), Google Calendar and Microsoft 365 rooms.

**Column mapping in config**, because every team's sheet looks different:
```yaml
source:
  type: excel
  path: /data/Room bookings.xlsx
  sheet: Bookings
  columns:
    room: "Room"
    date: "Date"
    start: "Start"
    end: "End"
    booked_by: "Booked by"
    title: "Meeting"        # optional
    protected: "Keep"       # optional, yes/no
```
Parsing is forgiving: dates in Excel serial or text formats (dd/mm/yyyy, yyyy-mm-dd), times as 14:00, 14h, 2pm, rows with merged cells or blank lines skipped with a warning.

**Shared-file safety (the hard part):**
- **Back up before every write** (timestamped copy in `backups/`, keep the last 30).
- If Excel's lock file (`~$Room bookings.xlsx`) exists, someone has the file open: wait and retry, never write over their session. After N retries, skip and say so in the log and the team channel.
- Re-read and compare the modified time right before writing; if the file changed since it was read, start over.
- Only touch the rows it decided to change. Keep formatting, other sheets, column widths and formulas outside the booking table.
- `--dry-run` prints exactly what it would remove, move or send, without writing anything.

## 4. Channels (v1)

Every channel only needs to "send text with links", so adding one stays small.

- **Slack:** a bot token sends DMs (best), or an incoming webhook posts to a channel.
- **Microsoft Teams:** a Workflows/incoming webhook posting to a channel (mentions by name).
- **Discord:** a channel webhook.
- **Email:** SMTP.
- **Generic webhook:** a JSON POST, which covers n8n, Make, Zapier and WhatsApp providers.

**People mapping:** "Booked by" holds a name or initials, so a `people` section (or a second sheet) maps them to a Slack ID, email, etc. Unknown names go to the team channel instead of a DM.

## 5. Keep / Release links

- Signed, single-use links that expire, served by a tiny built-in web endpoint (`/r/<token>`): Keep or Release, then a simple confirmation page.
- This is what makes it channel-agnostic: no Slack buttons, no Teams cards, just links.
- **No-server mode:** teams that don't want to expose an endpoint run it on a schedule (cron, GitHub Actions or Windows Task Scheduler) with links turned off; auto-release then does the job.

## 6. Running it

- **Docker:** `docker run -v ./config.yaml:/app/config.yaml -v "/path/to/shared folder":/data ghostbooking` starts the scheduler and the link endpoint.
- **pip / uv:** `ghostbooking run` (long-running) or `ghostbooking tick` (one pass, for cron).
- `ghostbooking check`: validates the config, reads the sheet, prints the parsed bookings and the people it can't match. This is the first thing a new team runs.
- Time zone per team (`Africa/Tunis`, `Europe/Paris`…), plus working days and hours.
- An audit log (JSON lines + a human-readable line) of every change and every message sent.

## 7. Stack

Python 3.12, `openpyxl` (Excel), `gspread` (Google Sheets), Pydantic settings + YAML config, APScheduler, a small Starlette/FastAPI app for links, `httpx` for channels, `itsdangerous` for signed tokens, pytest. Docker image + GitHub Actions CI. MIT licence.

## 8. Demo and proof

- `examples/`: a sample Excel booking file with ghost bookings, a double booking and a protected standup, plus a config that uses a "console" channel. `ghostbooking tick --dry-run` on it shows the whole story in the terminal.
- The README opens with a GIF: messy sheet → morning reset → clean sheet, then the Slack reminder and the Keep/Release link.
- Tests for parsing (date/time formats), conflict rules, protected rules, the lock-file/retry path and token signing.

## 9. Business

- Open source (MIT). Paid: installation for a team (fixed price), custom columns or rules, new channels, and support.
- The site's "Business automation" service links to it as proof, next to the meeting-room case study.
- No hosted version for now. Revisit only if teams ask for it.

## 10. Build order

1. Booking model + Excel/CSV source + column mapping + forgiving parsing (with tests).
2. Rules engine: reset, protected meetings, conflicts. `check` and `tick --dry-run` working on the example file.
3. Safe writes (backup, lock file, re-read check).
4. Channels: console, Slack, email, then Teams, Discord, webhook.
5. Reminders + Keep/Release links + auto-release + scheduler.
6. Google Sheets source.
7. Weekly summary, Docker, CI, README with GIF, release v0.1.0.
