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


def test_agy_provider_raises_llm_error_on_missing_binary(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file or directory: 'nonexistent-agy.exe'")

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="m", binary="nonexistent-agy.exe", retries=0)

    with pytest.raises(LLMError, match="nonexistent-agy.exe"):
        provider.complete("hello")


def test_agy_provider_unwraps_envelope_and_code_fence(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": '```json\n{"atoms": []}\n```',
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"atoms": []}


def test_agy_provider_unwraps_envelope_without_fence(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": '{"ok": true}',
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"ok": True}


def test_agy_provider_handles_trailing_tool_action_after_fence(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": (
            "```json\n"
            "{\n"
            '  "ok": true\n'
            "}\n"
            "```\n"
            '{"ok":true,"toolAction":"Completing the task","toolSummary":"Task completion"}'
        ),
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"ok": True}


def test_agy_provider_handles_text_before_fence(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": 'Here is the result:\n```json\n{"ok": true}\n```',
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"ok": True}


def test_agy_provider_handles_unfenced_json_with_trailing_noise(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": '{"ok": true}\n{"toolAction":"x"}',
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"ok": True}


def test_agy_provider_fence_content_containing_braces_in_strings(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": '```json\n{"title": "Uses {curly} braces ` here"}\n```',
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == {"title": "Uses {curly} braces ` here"}


def test_agy_provider_raises_on_envelope_error_status(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "ERROR",
        "error": "authentication failed",
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy", retries=0)
    with pytest.raises(LLMError, match="authentication failed"):
        provider.complete("extract", schema={"type": "object"})


def test_agy_provider_raises_on_empty_envelope_response(monkeypatch):
    envelope = {
        "conversation_id": "conv-123",
        "status": "SUCCESS",
        "response": "",
    }

    class Result:
        returncode = 0
        stdout = json.dumps(envelope)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy", retries=0)
    with pytest.raises(LLMError):
        provider.complete("extract", schema={"type": "object"})


def test_agy_provider_does_not_mistake_payload_with_status_for_envelope(monkeypatch):
    payload = {"status": "SUCCESS", "atoms": []}

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", lambda *args, **kwargs: Result())

    provider = AgyProvider(model="m", binary="agy")
    res = provider.complete("extract", schema={"type": "object"})
    assert res == payload


def test_agy_provider_translates_schema_path_on_wsl(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    fake_win_path = r"\\wsl.localhost\Ubuntu\tmp\translated_schema.json"
    captured_argv = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        captured_argv.append(cmd)
        if cmd[0] == "wslpath":
            return Result(stdout=f"{fake_win_path}\n")
        return Result(stdout=json.dumps({"ok": True}))

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="m", binary="agy.exe", retries=0)
    out = provider.complete("prompt", schema={"type": "object"})

    assert out == {"ok": True}
    assert len(captured_argv) == 2
    assert captured_argv[0][:2] == ["wslpath", "-w"]
    wslpath_arg = captured_argv[0][2]
    assert wslpath_arg.endswith(".json")
    agy_cmd = captured_argv[1]
    assert "--json-schema" in agy_cmd
    idx = agy_cmd.index("--json-schema")
    assert agy_cmd[idx + 1] == fake_win_path
    assert agy_cmd[idx + 1] != wslpath_arg


def test_agy_provider_does_not_translate_path_for_non_exe_binary(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")
    captured_argv = []

    class Result:
        returncode = 0
        stdout = json.dumps({"ok": True})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured_argv.append(cmd)
        return Result()

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="m", binary="agy", retries=0)
    out = provider.complete("prompt", schema={"type": "object"})

    assert out == {"ok": True}
    assert len(captured_argv) == 1
    assert not any(cmd[0] == "wslpath" for cmd in captured_argv)
    agy_cmd = captured_argv[0]
    idx = agy_cmd.index("--json-schema")
    schema_arg = agy_cmd[idx + 1]
    assert not schema_arg.startswith(r"\\wsl.localhost")
    # Asserting `not schema_arg.startswith("C:")` here tested the HOST, not the
    # code: on Windows the untranslated tempfile path IS a C:\ path, so the
    # assertion failed for reasons having nothing to do with translation. The
    # pass-through invariant is checked directly instead, on a path we control.
    # Compared against the path's own str() rather than a literal: a literal
    # would bake in a separator style and be just as host-dependent as the
    # assertion it replaces.
    untranslated = Path("/tmp/schema.json")
    assert provider._binary_path(untranslated) == str(untranslated)


def test_caching_provider_falls_back_to_inner_on_corrupt_cache(tmp_path: Path):
    inner = FakeProvider(["first", "second"])
    llm = CachingProvider(inner, cache_dir=tmp_path)

    assert llm.complete("prompt") == "first"

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_bytes(b"not-valid-json-{{{")

    result = llm.complete("prompt")
    assert result == "second"
    assert len(inner.prompts) == 2


def test_caching_provider_falls_back_to_inner_on_missing_response_key(tmp_path: Path):
    inner = FakeProvider(["first", "second"])
    llm = CachingProvider(inner, cache_dir=tmp_path)

    assert llm.complete("prompt") == "first"

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text(json.dumps({"unexpected": "shape"}), encoding="utf-8")

    result = llm.complete("prompt")
    assert result == "second"
    assert len(inner.prompts) == 2


def test_caching_provider_falls_back_to_inner_on_non_dict_cache(tmp_path: Path):
    inner = FakeProvider(["first", "second"])
    llm = CachingProvider(inner, cache_dir=tmp_path)

    assert llm.complete("prompt") == "first"

    cache_files = list(tmp_path.glob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    result = llm.complete("prompt")
    assert result == "second"
    assert len(inner.prompts) == 2


def test_agy_provider_retries_then_succeeds(monkeypatch):
    call_count = 0

    class FailResult:
        returncode = 1
        stdout = ""
        stderr = "temporary glitch"

    class SuccessResult:
        returncode = 0
        stdout = "success response"
        stderr = ""

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return FailResult()
        return SuccessResult()

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="m", binary="agy")
    result = provider.complete("hello")

    assert result == "success response"
    assert call_count == 3


def test_agy_provider_exhausts_retries_and_raises(monkeypatch):
    call_count = 0

    class FailResult:
        returncode = 1
        stdout = ""
        stderr = "permanent error"

    def fake_run(cmd, **kwargs):
        nonlocal call_count
        call_count += 1
        return FailResult()

    monkeypatch.setattr("jobhunt.llm.agy.subprocess.run", fake_run)

    provider = AgyProvider(model="m", binary="agy")

    with pytest.raises(LLMError, match="permanent error"):
        provider.complete("hello")

    assert call_count == 3
