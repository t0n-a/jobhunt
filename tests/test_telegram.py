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
        {"update_id": 1, "message": {"chat": {"id": 11}}},
        {"update_id": 2, "message": {"chat": {"id": 22}}},
    ]
    respx.post(f"https://api.telegram.org/bot{TOKEN}/getUpdates").respond(
        200, json={"ok": True, "result": result}
    )
    with httpx.Client() as http:
        assert discover_chat_id(TOKEN, http) == "22"


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
