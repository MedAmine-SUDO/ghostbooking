# 👻 Ghostbooking

**For startups that still book meeting rooms in a shared Excel file.**

Every morning, Ghostbooking clears the bookings people forgot to remove, tells people about double bookings with a free alternative, and posts a short summary to the team, on Slack, Teams, Discord, email or any webhook. Reminders before and after meetings come next. Nobody has to change how they book.

> Work in progress: steps 1–4 of [SPEC.md](SPEC.md) are done (reading the sheet, the cleanup rules, safe writes, channels). Reminders, Keep/Release links and the scheduler come next.

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

## Telling people

Map the names in "Booked by" to people, and pick your channels in `config.yaml`:

```yaml
people:
  Karim: {slack: U01KARIM, email: karim@example.com, aliases: [KB]}
  Omar: {email: omar@example.com}

channels:
  - type: slack
    token: ${SLACK_BOT_TOKEN}   # read from the environment, never stored in the file
    team_channel: "#rooms"
  - type: email
    host: smtp.example.com
    sender: rooms@example.com
```

| Channel | Direct messages | Team channel |
|---|---|---|
| `slack` (bot token) | yes, to people with a `slack` ID | `team_channel` |
| `slack` (`webhook_url`) | no | the webhook's channel, with an @mention |
| `email` (SMTP) | yes, to people with an `email` | `team_email` |
| `teams` (Workflows webhook) | no | yes, name in bold, links as buttons |
| `discord` (webhook) | no | yes, with an @mention for people with a `discord` ID |
| `webhook` (JSON POST) | everyone, with their whole `people:` entry | yes |
| `console` | prints everything, sends nothing | |

- People who aren't in `people:`, or can't be reached directly, get their message in the team channel with their name on it. `ghostbooking check` lists them.
- Each message is sent once. If a channel fails, the others still go out, the failure is logged, and the next run tries again (exit code 3).
- `--dry-run` shows every message and where it would go.
