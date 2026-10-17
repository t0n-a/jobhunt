import httpx
import pytest
import respx

from jobhunt.llm import LLMError
from jobhunt.llm.openai_compat import OpenAICompatProvider

URL = "https://api.example.com/v1/chat/completions"


def provider() -> OpenAICompatProvider:
    return OpenAICompatProvider(base_url="https://api.example.com/v1", api_key="k", model="m")


@respx.mock
def test_returns_text_without_a_schema():
    respx.post(URL).mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
    )

    assert provider().complete("hi") == "hello"


@respx.mock
def test_parses_json_when_a_schema_is_given():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": '{"ok": true}'}}]}
        )
    )

    assert provider().complete("hi", schema={"type": "object"}) == {"ok": True}


@respx.mock
def test_strips_a_markdown_fence_before_parsing():
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]},
        )
    )

    assert provider().complete("hi", schema={"type": "object"}) == {"ok": True}


@respx.mock
def test_http_error_becomes_llm_error():
    respx.post(URL).mock(return_value=httpx.Response(500, text="boom"))

    with pytest.raises(LLMError):
        provider().complete("hi")


@respx.mock
def test_api_key_is_sent_and_never_appears_in_the_error_message():
    route = respx.post(URL).mock(return_value=httpx.Response(401, text="unauthorized"))

    with pytest.raises(LLMError) as excinfo:
        provider().complete("hi")

    assert "k" not in str(excinfo.value).replace("Bearer", "")
    assert route.calls.last.request.headers["authorization"] == "Bearer k"


def test_retries_once_on_429_after_retry_after(respx_mock):
    route = respx_mock.post("https://x/v1/chat/completions").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}]}),
    ])
    slept = []
    p = OpenAICompatProvider("https://x/v1", "k", "m", sleep=slept.append)
    assert p.complete("q") == "hi"
    assert slept == [7] and route.call_count == 2

def test_second_429_raises(respx_mock):
    respx_mock.post("https://x/v1/chat/completions").mock(return_value=httpx.Response(429))
    p = OpenAICompatProvider("https://x/v1", "k", "m", sleep=lambda s: None)
    with pytest.raises(LLMError):
        p.complete("q")

def test_min_interval_spaces_calls(respx_mock):
    respx_mock.post("https://x/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "a"}}]}))
    slept, now = [], [100.0]
    p = OpenAICompatProvider("https://x/v1", "k", "m", min_interval_seconds=6,
                             sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)),
                             clock=lambda: now[0])
    p.complete("1"); now[0] += 2; p.complete("2")
    assert slept == [4]
