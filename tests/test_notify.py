import json
from datetime import date, datetime, time
from pathlib import Path
from typing import ClassVar

import httpx
import pytest
from pydantic import ValidationError

from ghostbooking.config import Config, load_config
from ghostbooking.messages import conflict_message, plan_messages, summary_message
from ghostbooking.models import Booking, Issue, Sheet
from ghostbooking.notify import Link, Message, People, build_notifier
from ghostbooking.rules import Conflict, Plan, Removal, plan_reset

MON = date(2026, 11, 23)


def cfg(tmp_path: Path, channels, people=None, **kw) -> Config:
    return Config(source={"path": Path("x.xlsx")}, channels=channels, people=people or {},
                  state=tmp_path / "state.json", log=tmp_path / "log.jsonl", **kw)


class Recorder:
    """An httpx transport that keeps every request and answers like the real services."""

    def __init__(self, slack_ok=True, status=200):
        self.requests: list[httpx.Request] = []
        self.slack_ok = slack_ok
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "slack.com":
            return httpx.Response(200, json={"ok": self.slack_ok, "error": "channel_not_found"})
        return httpx.Response(self.status, text="ok")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


PEOPLE = {
    "Karim": {"slack": "U1", "email": "karim@example.com", "aliases": ["KB"]},
    "Omar": {"email": "omar@example.com"},
}
SLACK = {"type": "slack", "token": "xoxb-test", "team_channel": "#rooms"}
MSG = Message(key="k1", kind="conflict", text="Room clash.", to="Karim",
              links=(Link("Keep", "https://x/k"),))


# --- config ------------------------------------------------------------------------------------
def test_people_match_names_aliases_case_and_spaces():
    people = People(Config(source={"path": "x"}, people=PEOPLE).people)
    assert people.lookup("  karim ").name == "Karim"
    assert people.lookup("kb").name == "Karim"
    assert people.lookup("Someone") is None
    assert people.lookup("") is None


def test_env_vars_are_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    (tmp_path / "c.yaml").write_text(
        "source: {path: x.xlsx}\nchannels:\n  - {type: slack, token: '${SLACK_BOT_TOKEN}'}\n"
        "  - {type: webhook, url: 'https://h/${MISSING:-default}'}\n", encoding="utf-8")
    c = load_config(tmp_path / "c.yaml")
    assert c.channels[0].token == "xoxb-secret"
    assert c.channels[1].url == "https://h/default"


def test_missing_env_var_is_a_clear_error(tmp_path):
    (tmp_path / "c.yaml").write_text(
        "source: {path: x.xlsx}\nchannels:\n  - {type: discord, webhook_url: '${NOPE_NOT_SET}'}\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="NOPE_NOT_SET"):
        load_config(tmp_path / "c.yaml")


def test_slack_needs_a_token_or_a_webhook():
    with pytest.raises(ValidationError, match="token"):
        Config(source={"path": "x"}, channels=[{"type": "slack"}])


def test_console_is_the_default_channel():
    assert [c.type for c in Config(source={"path": "x"}).channels] == ["console"]


# --- routing -----------------------------------------------------------------------------------
def test_known_person_gets_a_dm_on_every_channel_that_reaches_them(tmp_path):
    email = {"type": "email", "host": "smtp", "sender": "rooms@x"}
    n = build_notifier(cfg(tmp_path, [{"type": "console"}, SLACK, email], PEOPLE), http=Recorder().client())
    routes = [d.describe() for d in n.route(MSG)]
    assert routes == ["console → Karim", "slack → Karim", "email → Karim"]


def test_unknown_or_unreachable_people_go_to_the_team_channel(tmp_path):
    n = build_notifier(cfg(tmp_path, [SLACK], PEOPLE), http=Recorder().client())
    assert [d.describe() for d in n.route(Message("a", "conflict", "x", to="Nadia"))] == [
        "slack → team (for Nadia)"]
    omar = n.route(Message("b", "conflict", "x", to="Omar"))  # known, but no slack ID
    assert omar[0].to is None and omar[0].about.name == "Omar"


def test_nothing_reachable_is_reported_not_silently_dropped(tmp_path):
    email = {"type": "email", "host": "smtp", "sender": "rooms@x"}  # no team_email
    n = build_notifier(cfg(tmp_path, [{"type": "console"}, email]), smtp=FakeSMTP)
    out = n.send([Message("s", "summary", "Morning reset")])
    assert [m.kind for m in out.undeliverable] == ["summary"]


def test_a_message_is_sent_once(tmp_path):
    rec = Recorder()
    n = build_notifier(cfg(tmp_path, [SLACK], PEOPLE), http=rec.client())
    assert len(n.send([MSG]).sent) == 1
    assert n.send([MSG]).sent == []
    assert len(rec.requests) == 1
    assert "k1" in json.loads((tmp_path / "state.json").read_text())["sent"]


def test_failed_messages_are_retried_next_run(tmp_path):
    n = build_notifier(cfg(tmp_path, [SLACK], PEOPLE), http=Recorder(slack_ok=False).client())
    out = n.send([MSG])
    assert out.sent == [] and "channel_not_found" in out.failed[0][1]
    assert n.plan([MSG])  # still pending
    log = (tmp_path / "log.jsonl").read_text()
    assert "send_failed" in log


def test_one_failing_channel_does_not_stop_the_others(tmp_path):
    rec = Recorder(status=500)
    n = build_notifier(cfg(tmp_path, [SLACK, {"type": "discord", "webhook_url": "https://d/h"}], PEOPLE),
                       http=rec.client())
    out = n.send([Message("t", "summary", "hello")])
    assert [d.channel.name for d, _ in out.failed] == ["discord"]
    assert [d.channel.name for d in out.sent] == ["slack"]


def test_planning_writes_no_state(tmp_path):
    n = build_notifier(cfg(tmp_path, [SLACK], PEOPLE), http=Recorder().client())
    n.plan([MSG])
    assert not (tmp_path / "state.json").exists()


# --- channels ----------------------------------------------------------------------------------
def test_slack_bot_dm(tmp_path):
    rec = Recorder()
    build_notifier(cfg(tmp_path, [SLACK], PEOPLE), http=rec.client()).send([MSG])
    req = rec.requests[0]
    assert req.url == "https://slack.com/api/chat.postMessage"
    assert req.headers["authorization"] == "Bearer xoxb-test"
    assert rec.bodies()[0] == {"channel": "U1", "text": "Room clash.\n<https://x/k|Keep>",
                               "unfurl_links": False}


def test_slack_webhook_posts_to_its_channel_with_a_mention(tmp_path):
    rec = Recorder()
    hook = {"type": "slack", "webhook_url": "https://hooks.slack.com/x"}
    build_notifier(cfg(tmp_path, [hook], PEOPLE), http=rec.client()).send([MSG])
    assert rec.requests[0].url == "https://hooks.slack.com/x"
    assert rec.bodies()[0]["text"].startswith("<@U1> Room clash.")


def test_slack_text_is_escaped(tmp_path):
    rec = Recorder()
    build_notifier(cfg(tmp_path, [SLACK]), http=rec.client()).send(
        [Message("e", "summary", "R&D <board>")])
    assert rec.bodies()[0]["text"] == "R&amp;D &lt;board&gt;"


def test_discord_mentions_only_the_person(tmp_path):
    rec = Recorder()
    people = {"Karim": {"discord": "42"}}
    build_notifier(cfg(tmp_path, [{"type": "discord", "webhook_url": "https://d/h"}], people),
                   http=rec.client()).send([MSG])
    assert rec.bodies()[0] == {"content": "<@42> Room clash.\n[Keep](<https://x/k>)",
                               "allowed_mentions": {"users": ["42"]}}


def test_teams_posts_an_adaptive_card_with_link_buttons(tmp_path):
    rec = Recorder()
    build_notifier(cfg(tmp_path, [{"type": "teams", "webhook_url": "https://t/h"}], PEOPLE),
                   http=rec.client()).send([MSG])
    card = rec.bodies()[0]["attachments"][0]["content"]
    assert card["body"][0]["text"] == "**Karim**"
    assert card["actions"] == [{"type": "Action.OpenUrl", "title": "Keep", "url": "https://x/k"}]


def test_webhook_gets_the_person_and_the_links(tmp_path):
    rec = Recorder()
    people = {"Karim": {"phone": "+21612345678"}}  # extra fields pass through
    hook = {"type": "webhook", "url": "https://n8n/h", "headers": {"X-Token": "t"}}
    build_notifier(cfg(tmp_path, [hook], people), http=rec.client()).send([MSG])
    body = rec.bodies()[0]
    assert rec.requests[0].headers["x-token"] == "t"
    assert body["to"]["phone"] == "+21612345678" and body["to"]["name"] == "Karim"
    assert body["links"] == [{"label": "Keep", "url": "https://x/k"}]
    assert body["kind"] == "conflict" and body["key"] == "k1"


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout):
        self.host, self.port = host, port
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(f"login {user}")

    def send_message(self, msg):
        FakeSMTP.sent.append((self.calls, msg))


def test_email_to_a_person_and_to_the_team(tmp_path):
    FakeSMTP.sent = []
    email = {"type": "email", "host": "smtp.x", "username": "u", "password": "p",
             "sender": "rooms@x", "team_email": "office@x"}
    n = build_notifier(cfg(tmp_path, [email], PEOPLE), smtp=FakeSMTP)
    n.send([MSG, Message("t", "conflict", "x", to="Nadia", subject="Double booking")])
    (calls, dm), (_, team) = FakeSMTP.sent
    assert calls == ["starttls", "login u"]
    assert dm["To"] == "karim@example.com" and "Keep: https://x/k" in dm.get_content()
    assert team["To"] == "office@x" and team["Subject"] == "Double booking (for Nadia)"


# --- message wording ---------------------------------------------------------------------------
def bk(row, room, start, end, who, title=""):
    return Booking(row=row, room=room, day=MON, start=time(*start), end=time(*end), booked_by=who,
                   title=title)


def test_conflict_message_wording_and_stable_key():
    c = Conflict(MON, bk(7, "Atlas", (14,), (15,), "Ines"), bk(8, "Atlas", (14, 30), (15, 30), "Karim",
                 "Partner call"), "Kepler is free 14:30–15:30")
    m = conflict_message(c, Config(source={"path": "x"}), removed=False)
    assert m.to == "Karim"
    assert m.text == ("Heads up: your booking of Atlas on Mon 23 Nov at 14:30–15:30 (“Partner call”) "
                      "overlaps Ines's booking, 14:00–15:00, which was booked first. "
                      "Kepler is free 14:30–15:30.")
    assert "8" not in m.key  # rows move when the sheet is cleaned; the key must not
    gone = conflict_message(c, Config(source={"path": "x"}), removed=True)
    assert gone.text.startswith("Your booking of Atlas on Mon 23 Nov at 14:30–15:30 (“Partner call”) "
                                "was removed: it overlapped Ines's")


def test_summary_is_skipped_on_a_quiet_morning():
    assert summary_message(Plan(now=datetime(2026, 11, 23, 7, 30)), "b.xlsx") is None


def test_long_summaries_are_cut_short():
    plan = Plan(now=datetime(2026, 11, 23, 7, 30),
                removals=[Removal(bk(i, "Atlas", (9,), (10,), "A"), "old") for i in range(14)],
                issues=[Issue(20, "not a time: 'tbd'")])
    text = summary_message(plan, "b.xlsx").text
    assert "Cleared 14 ghost bookings:" in text and "• and 4 more" in text
    assert "1 row I couldn't read" in text


def test_example_plan_messages(example):
    from ghostbooking.sources import open_source

    c = load_config(example)
    sheet: Sheet = open_source(c.source, c.backups).read()
    msgs = plan_messages(plan_reset(sheet, c, datetime(2026, 11, 23, 7, 30)), c, "room-bookings.xlsx")
    assert [(m.kind, m.to) for m in msgs] == [("conflict", "Karim"), ("summary", None)]
    c.notifications.morning_summary = False
    assert len(plan_messages(plan_reset(sheet, c, datetime(2026, 11, 23, 7, 30)), c, "b")) == 1


def test_summary_goes_out_once_a_day_unless_more_is_cleared(tmp_path):
    rec = Recorder()
    n = build_notifier(cfg(tmp_path, [SLACK]), http=rec.client())
    now = datetime(2026, 11, 23, 7, 30)
    ghost = Removal(bk(3, "Orion", (10,), (11,), "Karim"), "old")
    issue = [Issue(13, "not a time: 'tbd'")]

    def run(removals):
        return n.send([summary_message(Plan(now=now, removals=removals, issues=issue), "b")])

    assert len(run([ghost]).sent) == 1
    assert run([]).sent == []  # rerun, only the old unreadable row: already reported today
    assert run([ghost]).sent == []
    assert len(run([Removal(bk(4, "Atlas", (9,), (10,), "Sara"), "old")]).sent) == 1  # new removal
