import json
import subprocess
import tempfile
from pathlib import Path

from jobhunt.llm.base import LLMError


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
                cmd += ["json", "--json-schema", str(schema_file)]
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
                return json.loads(output)
            except json.JSONDecodeError as exc:
                raise LLMError(f"agy returned non-JSON output: {output[:200]}") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"agy timed out after {self.timeout_seconds}s") from exc
        except OSError as exc:
            raise LLMError(f"agy binary '{self.binary}' failed to execute: {exc}") from exc
        finally:
            if schema_file is not None:
                schema_file.unlink(missing_ok=True)
