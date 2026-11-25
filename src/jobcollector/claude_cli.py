"""run_claude(): the only place that invokes claude -p."""

import json
import subprocess
import tempfile
from typing import Any


class ClaudeError(Exception):
    """Base class. Every failure means: leave unscored and retry next run."""


class ClaudeNotFoundError(ClaudeError):
    """The claude executable could not be started."""


class ClaudeTimeoutError(ClaudeError):
    """The CLI did not finish in time and was killed."""


class ClaudeExitError(ClaudeError):
    """The CLI exited with a non-zero code."""

    def __init__(self, returncode: int, stderr: str, stdout: str) -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout
        # With JSON output the reason, such as a usage limit, can be on stdout.
        detail = " | ".join(text.strip() for text in (stderr, stdout) if text.strip())
        super().__init__(f"claude exited with code {returncode}: {detail[:2000]}")


class ClaudeReportedError(ClaudeError):
    """The CLI exited normally but its output has is_error set."""


class ClaudeOutputError(ClaudeError):
    """The output is not the expected JSON."""

    def __init__(self, message: str, output: str) -> None:
        self.output = output
        super().__init__(f"{message}: {output[:500]!r}")


def run_claude(prompt: str, schema: dict[str, Any], model: str, timeout: int) -> dict[str, Any]:
    """Return the model's JSON answer, using the CLI's subscription login."""
    # Runs in an empty directory so no CLAUDE.md reaches the prompt. On Windows
    # a killed process can briefly hold the directory open.
    with tempfile.TemporaryDirectory(
        prefix="jobcollector-", ignore_cleanup_errors=True
    ) as directory:
        try:
            completed = subprocess.run(
                _command(schema, model),
                input=prompt,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                cwd=directory,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ClaudeTimeoutError(f"claude did not finish in {timeout} seconds") from error
        except OSError as error:
            raise ClaudeNotFoundError(f"could not start claude: {error}") from error
    if completed.returncode != 0:
        raise ClaudeExitError(completed.returncode, completed.stderr, completed.stdout)
    return _parse(completed.stdout)


def _command(schema: dict[str, Any], model: str) -> list[str]:
    """Build the command. Prompts hold untrusted text, so all tools are off."""
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(schema),
        "--tools",
        "",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]


def _parse(stdout: str) -> dict[str, Any]:
    envelope = _json_object(stdout, "claude output")
    if envelope.get("is_error"):
        raise ClaudeReportedError(str(envelope.get("result") or envelope.get("subtype")))
    structured = envelope.get("structured_output")
    if structured is None:
        result = envelope.get("result")
        if not isinstance(result, str):
            raise ClaudeOutputError("no structured_output or result", stdout)
        return _json_object(result, "claude result")
    if not isinstance(structured, dict):
        raise ClaudeOutputError("structured_output is not an object", stdout)
    return structured


def _json_object(text: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError as error:
        raise ClaudeOutputError(f"{name} is not valid JSON", text) from error
    if not isinstance(value, dict):
        raise ClaudeOutputError(f"{name} is not a JSON object", text)
    return value
