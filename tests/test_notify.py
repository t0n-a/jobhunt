import httpx
import respx

from jobhunt.coach.notify import render_task_line, send_telegram


def test_render_includes_the_task_the_why_and_the_reward():
    today = {
        "step": type("S", (), {"title": "Write the manifest"})(),
        "minutes": 45,
        "why": "Kubernetes is missing from 14 of your best-fit jobs.",
        "reward": "Deployed a Python service to k3s.",
    }

    line = render_task_line(today)

    assert "Write the manifest" in line
    assert "45" in line
    assert "14" in line
    assert "Deployed a Python service" in line


def test_render_with_no_task_says_so_without_guilt():
    line = render_task_line({"step": None, "minutes": 15, "why": "", "reward": ""})

    assert "nothing" in line.casefold()
    assert "streak" not in line.casefold()
    assert "missed" not in line.casefold()


@respx.mock
def test_send_telegram_posts_to_the_bot_api():
    route = respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )

    assert send_telegram("TOKEN", "123", "hello") is True
    assert route.called


@respx.mock
def test_send_telegram_returns_false_on_failure_rather_than_raising():
    """A failed notification must not break a scan or a scheduled run."""
    respx.post(url__startswith="https://api.telegram.org").mock(
        return_value=httpx.Response(401, json={"ok": False})
    )

    assert send_telegram("BAD", "123", "hello") is False


@respx.mock
def test_send_telegram_survives_a_network_error():
    respx.post(url__startswith="https://api.telegram.org").mock(
        side_effect=httpx.ConnectError("down")
    )

    assert send_telegram("TOKEN", "123", "hello") is False
