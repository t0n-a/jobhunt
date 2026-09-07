import json
from pathlib import Path

import pytest

from jobhunt.llm import CachingProvider, FakeProvider, LLMError
from jobhunt.llm.agy import AgyProvider


def test_fake_provider_returns_queued_responses_in_order():
    llm = FakeProvider(["first", "second"])

    assert llm.complete("a") == "first"
    assert llm.complete("b") == "second"
    assert llm.prompts == ["a", "b"]


def test_fake_provider_raises_when_exhausted():
    llm = FakeProvider(["only"])
    llm.complete("a")

    with pytest.raises(LLMError):
        llm.complete("b")


def test_caching_provider_calls_inner_once_for_identical_prompt(tmp_path: Path):
    inner = FakeProvider(["cached-answer"])
    llm = CachingProvider(inner, cache_dir=tmp_path)

    first = llm.complete("same prompt")
    second = llm.complete("same prompt")

    assert first == second == "cached-answer"
    assert len(inner.prompts) == 1


def test_caching_provider_distinguishes_different_schemas(tmp_path: Path):
    inner = FakeProvider([{"a": 1}, {"b": 2}])
    llm = CachingProvider(inner, cache_dir=tmp_path)

    one = llm.complete("p", schema={"type": "object"})
    two = llm.complete("p", schema={"type": "array"})

    assert one != two
    assert len(inner.prompts) == 2


def test_agy_provider_builds_expected_command(monkeypatch, tmp_path: Path):
    captured = {}

    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return Result()

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="gemini-3.8-flash-medium", binary="agy.exe")
    out = provider.complete("hello", schema={"type": "object"})

    assert out == {"ok": True}
    assert captured["cmd"][0] == "agy.exe"
    assert "-p" in captured["cmd"]
    assert "--model" in captured["cmd"]
    assert "gemini-3.8-flash-medium" in captured["cmd"]
    assert "--json-schema" in captured["cmd"]


def test_agy_provider_raises_llm_error_on_nonzero_exit(monkeypatch):
    class Result:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda cmd, **kw: Result())

    provider = AgyProvider(model="m", binary="agy.exe", retries=0)

    with pytest.raises(LLMError, match="boom"):
        provider.complete("hello")
