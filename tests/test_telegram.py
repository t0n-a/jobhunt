import json
import logging
from types import SimpleNamespace

import httpx
import pytest
import respx

from jobhunt.telegram import (
    MAX_TEXT,
    TelegramClient,
    discover_chat_id,
    outreach_message,
    parse_command,
    queue_message,
)

TOKEN = "123:SECRET"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes 12", ("yes", 12)),
        ("/YES@jobhunt_bot 12", ("yes", 12)),
        ("yes:12", ("yes", 12)),
        ("pause", ("pause", None)),
        ("replied 3", ("replied", 3)),
        ("interview 3", ("interview", 3)),
        ("offer 3", ("offer", 3)),
        ("rejected 3", ("rejected", 3)),
        ("autopilot on", ("autopilot", "on")),
        ("/Autopilot OFF", ("autopilot", "off")),
        ("autopilot", None),
        ("unblock jobs.lever.co", ("unblock", "jobs.lever.co")),
        ("/unblock Jobs.Lever.co", ("unblock", "jobs.lever.co")),
        ("unblock a/b", None),
        ("unblock", None),
        ("autopilot maybe", None),
        ("yes", None),
        ("hello", None),
        ("sent twelve", None),
    ],
)
def test_parse_command(text, expected):
    assert parse_command(text) == expected


@respx.mock
def test_send_truncates_and_sends_buttons():
    route = respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").respond(200)
    buttons = [[{"text": "Yes", "callback_data": "yes:1"}]]
    with httpx.Client() as http:
        assert TelegramClient(TOKEN, "5", http).send("x" * 5000, buttons) is True
    body = json.loads(route.calls[0].request.content)
    assert len(body["text"]) == MAX_TEXT
    assert body["reply_markup"] == {"inline_keyboard": buttons}


@respx.mock
def test_send_failure_returns_false():
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").respond(500)
    with httpx.Client() as http:
        assert TelegramClient(TOKEN, "5", http).send("hi") is False


@respx.mock
def test_updates_error_is_empty():
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").mock(
        side_effect=httpx.ConnectError("down")
    )
    with httpx.Client() as http:
        assert TelegramClient(TOKEN, "5", http).updates(None) == []


@respx.mock
def test_discover_chat_id():
    result = [
        {"update_id": 1, "message": {"chat": {"id": 11, "type": "private"}, "text": "123456"}},
        {"update_id": 2, "message": {"chat": {"id": 22, "type": "group"}, "text": "123456"}},
        {"update_id": 3, "message": {"chat": {"id": 33, "type": "private"}, "text": "nope"}},
    ]
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(
        200, json={"ok": True, "result": result}
    )
    with httpx.Client() as http:
        assert discover_chat_id(TOKEN, http, "123456") == "11"


def test_queue_message_shape():
    app = SimpleNamespace(id=7)
    job = SimpleNamespace(title="Dev", company="Acme", location=None, url="http://j")
    facts = SimpleNamespace(pay_yearly_eur=28000, pay_estimated=True)
    text, buttons = queue_message(app, job, SimpleNamespace(fit_score=80), facts, "http://w")
    assert "€28,000 (est.)" in text and "fit 80" in text and "location ?" in text
    assert "Review: http://w/applications/7" in text
    assert [b["callback_data"] for b in buttons[0]] == ["yes:7", "no:7"]
    assert "pay ? | fit ?" in queue_message(app, job, None, None, "w")[0]


@respx.mock
def test_token_never_logged(caplog):
    respx.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage").respond(200)
    with caplog.at_level(logging.INFO), httpx.Client() as http:
        TelegramClient(TOKEN, "5", http).send("hi")
    assert TOKEN not in caplog.text


def test_queue_message_strips_control_and_bidi_characters():
    app = SimpleNamespace(id=1)
    job = SimpleNamespace(
        title="Dev\u202eevil", company="Acme\u2066x\x07", location="Po\u200eto", url="http://j"
    )
    text, _ = queue_message(app, job, None, None, "http://w")
    assert "\u202e" not in text and "\u2066" not in text and "\x07" not in text
    assert "\u200e" not in text
    assert "Devevil @ Acmex" in text


def test_queue_message_autopilot_note():
    app = SimpleNamespace(id=1)
    job = SimpleNamespace(title="T", company="C", location="", url="u")
    text, _ = queue_message(app, job, None, None, "http://w", "autopilot stopped: captcha")
    assert text.endswith("\nAutopilot: stopped: captcha")


def test_stopped_note_is_cleaned_and_capped():
    from types import SimpleNamespace

    app = SimpleNamespace(id=1)
    job = SimpleNamespace(title="Dev", company="A", location=None, url="u")
    text, _ = queue_message(app, job, None, None, "http://x", note="autopilot stopped: field \u202eevil" + "x" * 500)
    line = text.splitlines()[-1]
    assert "\u202e" not in line and len(line) <= len("Autopilot: ") + 200


def test_queue_message_shows_autopilot_notes():
    app = SimpleNamespace(id=1)
    job = SimpleNamespace(title="Dev", company="A", location=None, url="http://j")
    t, _ = queue_message(app, job, None, None, "w", "autopilot stopped: captcha")
    assert "Autopilot: stopped: captcha" in t
    t, _ = queue_message(app, job, None, None, "w", "autopilot error: X - it may have gone through")
    assert "Autopilot: error: X - it may have gone through" in t


def _outreach(text):
    o = SimpleNamespace(id=5, kind="message", text=text)
    job = SimpleNamespace(title="Dev", company="Acme")
    contact = SimpleNamespace(name="Ana", role="Recruiter", profile_url="https://linkedin.com/in/ana")
    return outreach_message(o, job, contact, "http://web")


def test_outreach_message_has_copy_button_when_short():
    (header, buttons), (body, none) = _outreach("Hi Ana")
    assert header.splitlines()[0] == "Outreach #5 (message): Dev @ Acme"
    assert "To: Ana, Recruiter" in header and "Copy the text below" in header
    assert buttons[0] == [{"text": "Copy", "copy_text": {"text": "Hi Ana"}}]
    assert [b["callback_data"] for b in buttons[1]] == ["osent:5", "oskip:5"]
    assert body == "Hi Ana" and none is None


def test_long_text_has_no_copy_button_but_its_own_message():
    text = "x" * 257
    (_, buttons), (body, _) = _outreach(text)
    assert len(buttons) == 1 and "copy_text" not in str(buttons)
    assert body == text


def test_parse_outreach_commands():
    assert parse_command("osent 5") == ("osent", 5)
    assert parse_command("oskip:5") == ("oskip", 5)
    assert parse_command("/oreplied 5") == ("oreplied", 5)
    assert parse_command("osent") is None
