# Changelog

## 0.1.1 (unreleased)

- Published on PyPI: `pipx install ghostbooking` (`[google]` extra for Google Sheets).
- README works on PyPI too (absolute image link, install section).
- Package metadata: SPDX licence with the licence file included, project links.

## 0.1.0 (2026-11-20)

First release.

- Sources: Excel (.xlsx), CSV, Google Sheets. Column mapping in `config.yaml`; forgiving dates and times ("14h", "2pm", "Fri 20/11/2026", "Every Monday", "tous les lundis").
- Morning reset: removes bookings left over from earlier days, keeps recurring and protected meetings, flags double bookings with a free room or slot.
- Safe writes: a backup before every change, never writes while the file is open in Excel or LibreOffice, re-checks before writing, keeps formatting. Google Sheets: re-checks rows before deleting.
- Channels: Slack, Microsoft Teams, Discord, email, generic webhook, console. People mapping with aliases; unknown people get their message in the team channel. Each message is sent once.
- Reminders before meetings with signed Keep / Release links, no-show auto-release, bookings freed when they end.
- Morning and weekly summaries for the team. Messages use each app's own formatting (bold headings, short lines) and can link to the sheet (`source.label`, `source.url`).
- `check`, `tick`, `run`; Docker image; audit log.
