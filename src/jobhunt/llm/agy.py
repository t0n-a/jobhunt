import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from jobhunt.llm.base import LLMError


def _extract_balanced_json(text: str) -> object | None:
    for start_idx, start_ch in enumerate(text):
        if start_ch not in ("{", "["):
            continue

        stack = ["}" if start_ch == "{" else "]"]
        in_string = False
        escape = False

        for i in range(start_idx + 1, len(text)):
            ch = text[i]

            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    stack.append("}")
                elif ch == "[":
                    stack.append("]")
                elif ch in ("}", "]"):
                    if not stack or ch != stack[-1]:
                        break
                    stack.pop()
                    if not stack:
                        candidate = text[start_idx : i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
    return None


def _unwrap_response(response_val: str) -> object:
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)\n?\s*```",
        response_val,
        re.DOTALL | re.IGNORECASE,
    )
    if fence_match:
        fenced_content = fence_match.group(1).strip()
        try:
            return json.loads(fenced_content)
        except json.JSONDecodeError:
            balanced = _extract_balanced_json(fenced_content)
            if balanced is not None:
                return balanced
    else:
        stripped = response_val.strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

        balanced = _extract_balanced_json(stripped)
        if balanced is not None:
            return balanced

    raise LLMError(f"agy returned non-JSON response: {response_val[:200]}")


class AgyProvider:
    """Calls the Antigravity CLI. Works from WSL via the agy.exe interop shim."""

    def __init__(
        self,
        model: str,
        binary: str = "agy.exe",
        timeout_seconds: int = 300,
        retries: int = 2,
    ):
        self.model = model
        self.binary = binary
        self.timeout_seconds = timeout_seconds
        self.retries = retries

    def _binary_path(self, path: Path) -> str:
        path_str = str(path)
        if sys.platform.startswith("linux") and self.binary.endswith(".exe"):
            try:
                res = subprocess.run(
                    ["wslpath", "-w", path_str],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip():
                    return res.stdout.strip()
            except OSError:
                pass
        return path_str

    def complete(self, prompt: str, schema: dict | None = None) -> str | dict:
        last_error = ""
        for _ in range(self.retries + 1):
            try:
                return self._run_once(prompt, schema)
            except LLMError as exc:
                last_error = str(exc)
        raise LLMError(last_error)

    def _run_once(self, prompt: str, schema: dict | None) -> str | dict:
        schema_file: Path | None = None
        try:
            cmd = [self.binary, "-p", prompt, "--model", self.model, "--output-format"]
            if schema is not None:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                ) as fd:
                    schema_file = Path(fd.name)
                    json.dump(schema, fd)
                cmd += ["json", "--json-schema", self._binary_path(schema_file)]
            else:
                cmd += ["text"]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if result.returncode != 0:
                raise LLMError(f"agy exited {result.returncode}: {result.stderr.strip()}")

            output = result.stdout.strip()
            if schema is None:
                return output
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError as exc:
                raise LLMError(f"agy returned non-JSON output: {output[:200]}") from exc

            is_envelope = (
                isinstance(parsed, dict)
                and "status" in parsed
                and "conversation_id" in parsed
                and ("response" in parsed or "error" in parsed)
            )
            if is_envelope:
                status = parsed.get("status")
                if status != "SUCCESS":
                    err_msg = parsed.get("error") or ""
                    msg = (
                        f"agy returned status {status}: {err_msg}".strip()
                        if err_msg
                        else f"agy returned status {status}"
                    )
                    raise LLMError(msg)

                response_val = parsed.get("response")
                if not isinstance(response_val, str) or not response_val.strip():
                    # Headless agy auto-denies any tool the model reaches for and
                    # says so only on stderr; without it this is indistinguishable
                    # from a model that said nothing.
                    reason = result.stderr.strip()
                    raise LLMError(
                        f"agy returned an empty response: {reason}"
                        if reason
                        else "agy returned an empty response"
                    )

                return _unwrap_response(response_val)

            return parsed
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"agy timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise LLMError(f"agy binary '{self.binary}' failed to execute: {exc}") from exc
        finally:
            if schema_file is not None:
                schema_file.unlink(missing_ok=True)
