"""A whole Monday on the example sheet: reset, reminders, Keep, Release, no-shows, end of meetings."""

import json
import threading
from datetime import date, datetime, time

import httpx
import pytest

from ghostbooking.config import Links, WebhookChannel, load_config
from ghostbooking.engine import Engine
from ghostbooking.links import BadLink, LinkData, make_token, read_token
from ghostbooking.web import make_server

SECRET = "a-test-secret-of-20+chars"


def at(hm: str) -> datetime:
    return datetime.fromisoformat(f"2026-11-23 {hm}")


class Hook:
    """Stands in for the team's webhook receiver."""

    def __init__(self):
        self.got: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.got.append(json.loads(request.content))
        return httpx.Response(200)

    def kinds(self) -> list[tuple[str, str | None]]:
        out = [(m["kind"], (m["to"] or {}).get("name")) for m in self.got]
        self.got.clear()
        return out

    def link(self, label: str, who: str) -> str:
        for m in self.got:
            if (m["to"] or {}).get("name") == who:
                for link in m["links"]:
                    if link["label"] == label:
                        return link["url"].rsplit("/r/", 1)[1]
        raise AssertionError(f"no {label} link for {who}")


@pytest.fixture
def day(example):
    cfg = load_config(example)
    cfg.channels = [WebhookChannel(type="webhook", url="https://hook.test/")]
    cfg.links = Links(base_url="https://rooms.test", secret=SECRET, listen="127.0.0.1:0")
    hook = Hook()
    engine = Engine(cfg, http=httpx.Client(transport=httpx.MockTransport(hook)))
    return engine, hook


def rooms(engine) -> list[tuple[str, str]]:
    sheet = engine.source().read()
    return sorted((b.room, f"{b.start:%H:%M} {b.booked_by}") for b in sheet.bookings
                  if b.day == date(2026, 11, 23))


def test_a_whole_monday(day):
    engine, hook = day
    # 07:30 morning reset: Friday's leftovers go, the team gets a summary, Karim hears about the clash.
    p = engine.run_pass(at("07:30"))
    assert p.is_reset and len(p.plan.removals) == 3
    assert hook.kinds() == [("conflict", "Karim"), ("summary", None)]
    assert engine.run_pass(at("07:31")).quiet  # nothing new; the reset is done for today

    # 09:50 reminder for Youssef's 10:00. The recurring 09:30 standup gets none (it's the default).
    engine.run_pass(at("09:50"))
    assert hook.kinds() == [("reminder", "Youssef")]

    # 13:50 reminders for Ines and Sara at 14:00; Youssef's 10:00–11:00 has ended and is freed.
    p = engine.run_pass(at("13:50"))
    assert [r.reason for r in p.plan.removals] == ["ended at 11:00 today"]
    assert ("Atlas", "10:00 Youssef") not in rooms(engine)
    ines_keep = hook.link("Keep it", "Ines")
    sara_text = next(m["text"] for m in hook.got if m["to"]["name"] == "Sara")
    assert "Tap Keep by 14:15, or the room is released for others." in sara_text
    hook.kinds()

    # Ines taps Keep. Sara doesn't, so at 14:15 her room is released and she's told.
    title, _ = engine.answer(read_token(SECRET, ines_keep, at("13:55")), at("13:55"))
    assert title == "Kept"
    p = engine.run_pass(at("14:15"))
    assert [(r.kind, r.booking.booked_by) for r in p.plan.removals] == [("no_show", "Sara")]
    assert hook.kinds() == [("no_show", "Sara")]
    assert ("Atlas", "14:00 Ines") in rooms(engine) and ("Orion", "14:00 Sara") not in rooms(engine)

    # 14:20 Karim's reminder; he releases the room and it's gone from the sheet at once.
    engine.run_pass(at("14:20"))
    release = hook.link("Release the room", "Karim")
    hook.kinds()
    title, text = engine.answer(read_token(SECRET, release, at("14:21")), at("14:21"))
    assert title == "Released" and "free again" in text
    assert ("Atlas", "14:30 Karim") not in rooms(engine)
    assert engine.answer(read_token(SECRET, release, at("14:22")), at("14:22"))[0] == "Already free"

    # 15:00 Ines's meeting ends and frees itself.
    engine.run_pass(at("15:00"))
    assert ("Atlas", "14:00 Ines") not in rooms(engine)


def test_release_while_the_file_is_open_waits_for_it(day):
    engine, hook = day
    engine.run_pass(at("07:30"))
    engine.run_pass(at("13:50"))
    token = hook.link("Release the room", "Sara")
    lock = engine.cfg.source.path.with_name("~$" + engine.cfg.source.path.name)
    lock.touch()
    _, text = engine.answer(read_token(SECRET, token, at("13:51")), at("13:51"))
    assert "open in Excel right now" in text
    assert ("Orion", "14:00 Sara") in rooms(engine)
    lock.unlink()
    p = engine.run_pass(at("13:52"))
    assert [r.kind for r in p.plan.removals] == ["released"]
    assert ("Orion", "14:00 Sara") not in rooms(engine)
    assert "released" not in engine.state.read() or not engine.state.section("released")


def test_reminders_still_go_out_when_the_file_is_open(day):
    engine, hook = day
    engine.run_pass(at("07:30"))
    hook.kinds()
    engine.cfg.source.path.with_name("~$" + engine.cfg.source.path.name).touch()
    p = engine.run_pass(at("13:50"))
    assert p.skipped
    assert sorted(hook.kinds()) == [("reminder", "Ines"), ("reminder", "Sara")]


def no_shows(p):
    return [r for r in p.plan.removals if r.kind == "no_show"]


def test_no_auto_release_for_people_who_never_got_a_reminder(day):
    engine, _ = day
    engine.run_pass(at("07:30"))
    assert not no_shows(engine.run_pass(at("14:20")))  # Sara's 14:00 reminder was never sent


def test_no_auto_release_when_set_to_keep(day):
    engine, hook = day
    engine.cfg.links.no_answer = "keep"
    engine.run_pass(at("07:30"))
    hook.kinds()
    engine.run_pass(at("13:50"))
    assert all("Release it so others can use it" in m["text"] for m in hook.got)
    assert not no_shows(engine.run_pass(at("14:20")))


def test_reset_runs_once_on_working_days_only(day):
    engine, _ = day
    assert not engine.reset_due(at("07:29"))
    assert engine.reset_due(at("07:30"))
    engine.run_pass(at("07:30"))
    assert not engine.reset_due(at("12:00"))
    assert not engine.reset_due(datetime(2026, 11, 28, 9, 0))  # Saturday
    assert engine.run_pass(at("12:00"), force_reset=True).is_reset


def test_without_release_ended_meetings_stay_until_the_next_reset(day):
    engine, _ = day
    engine.cfg.rules.release_ended = False
    engine.run_pass(at("07:30"))
    assert engine.run_pass(at("11:30")).plan.removals == []
    assert ("Atlas", "10:00 Youssef") in rooms(engine)


def test_recurring_reminders_have_no_links(day):
    engine, hook = day
    engine.cfg.reminders.recurring = True
    engine.run_pass(at("07:30"))
    hook.kinds()
    engine.run_pass(at("09:25"))
    (standup,) = hook.got
    assert standup["to"]["name"] == "Lina" and standup["links"] == []


def test_no_links_configured_means_plain_reminders(day):
    engine, hook = day
    engine.cfg.links = None
    engine.run_pass(at("07:30"))
    hook.kinds()
    engine.run_pass(at("13:50"))
    assert all(m["links"] == [] for m in hook.got)
    assert not [r for r in engine.run_pass(at("14:30")).plan.removals if r.kind == "no_show"]


def test_dry_run_changes_nothing(day):
    engine, hook = day
    before = engine.cfg.source.path.read_bytes()
    p = engine.run_pass(at("13:50"), dry_run=True)
    assert p.planned and not hook.got
    assert engine.cfg.source.path.read_bytes() == before
    assert not engine.cfg.state.exists()


# --- tokens ------------------------------------------------------------------------------------
DATA = LinkData("release", "Atlas", date(2026, 11, 23), time(14), time(15), "Karim", "Partner call")


def test_tokens_round_trip_and_cannot_be_forged():
    token = make_token(SECRET, DATA)
    assert read_token(SECRET, token, at("14:10")) == DATA
    with pytest.raises(BadLink, match="isn't valid"):
        read_token("another-secret-of-20chars", token, at("14:10"))
    forged = make_token(SECRET, LinkData("release", "Atlas", DATA.day, time(14), time(15), "Ines"))
    with pytest.raises(BadLink, match="isn't valid"):
        read_token(SECRET, forged.split(".")[0] + "." + token.split(".")[1], at("14:10"))
    with pytest.raises(BadLink, match="isn't valid"):
        read_token(SECRET, token[:-3], at("14:10"))


def test_tokens_expire_when_the_meeting_ends():
    with pytest.raises(BadLink, match="ended at 15:00"):
        read_token(SECRET, make_token(SECRET, DATA), at("15:00"))


def test_links_config_needs_a_real_secret():
    with pytest.raises(ValueError):
        Links(base_url="https://x", secret="short")


# --- web endpoint ------------------------------------------------------------------------------
def test_opening_a_link_changes_nothing_only_the_button_does(day):
    engine, hook = day
    engine.run_pass(at("07:30"))
    engine.run_pass(at("13:50"))
    token = hook.link("Release the room", "Sara")
    server = make_server(engine, lambda: at("13:52"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        r = httpx.get(f"{base}/r/{token}")
        assert r.status_code == 200 and "Release this room?" in r.text and 'method="post"' in r.text
        assert ("Orion", "14:00 Sara") in rooms(engine)  # a preview bot opening it does nothing
        r = httpx.post(f"{base}/r/{token}")
        assert "free again" in r.text
        assert ("Orion", "14:00 Sara") not in rooms(engine)
        assert httpx.get(f"{base}/r/{token[:-4]}").status_code == 400
        assert httpx.get(f"{base}/nope").status_code == 404
        assert httpx.get(f"{base}/health").text == "ok"
    finally:
        server.shutdown()


def test_page_escapes_booking_text(day):
    engine, _ = day
    data = LinkData("keep", "<b>Atlas</b>", date(2026, 11, 23), time(14), time(15), "K")
    server = make_server(engine, lambda: at("13:52"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = httpx.get(f"http://127.0.0.1:{server.server_address[1]}/r/{make_token(SECRET, data)}")
        assert "&lt;b&gt;Atlas&lt;/b&gt;" in r.text and "<b>Atlas" not in r.text
    finally:
        server.shutdown()
