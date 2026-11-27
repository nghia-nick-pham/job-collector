"""run_claude(): the only place that invokes claude -p."""

import json
import re
import subprocess
import tempfile
from typing import Any


_AUTH = re.compile(
    r"not logged in|/login|log ?in again|invalid api key|authenticat|unauthorized"
    r"|(?:token|login|session) (?:has |is )?expired",
    re.IGNORECASE,
)
_USAGE_LIMIT = re.compile(
    r"(?:usage|rate|session|weekly|daily|\d+-hour|opus|sonnet) limit|hit your limit|limit reached"
    r"|too many requests",
    re.IGNORECASE,
)


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

    def __init__(self, message: str, status: int | None = None) -> None:
        self.status = status
        super().__init__(message)


class ClaudeOutputError(ClaudeError):
    """The output is not the expected JSON."""

    def __init__(self, message: str, output: str) -> None:
        self.output = output
        super().__init__(f"{message}: {output[:500]!r}")


def failure_reason(error: ClaudeError) -> str:
    """Name the cause: auth, usage limit, timeout, or something less specific.

    Only the CLI's own message is searched, because its JSON always contains
    words such as "usage". Unrecognized wording is reported as "CLI error".
    """
    if isinstance(error, ClaudeTimeoutError):
        return "timeout"
    if isinstance(error, ClaudeNotFoundError):
        return "claude not found"
    if isinstance(error, ClaudeOutputError):
        return "invalid output"
    status, message = _cli_message(error)
    if status in (401, 403) or _AUTH.search(message):
        return "auth"
    if status == 429 or _USAGE_LIMIT.search(message):
        return "usage limit"
    return "CLI error"


def _cli_message(error: ClaudeError) -> tuple[int | None, str]:
    """Return the API status, if any, and the CLI's error message."""
    if isinstance(error, ClaudeReportedError):
        return error.status, str(error)
    if not isinstance(error, ClaudeExitError):
        return None, str(error)
    try:
        envelope = json.loads(error.stdout)
    except ValueError:
        return None, f"{error.stderr}\n{error.stdout}"
    if not isinstance(envelope, dict):
        return None, error.stderr
    return envelope.get("api_error_status"), f"{error.stderr}\n{envelope.get('result')}"


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
        message = str(envelope.get("result") or envelope.get("subtype"))
        raise ClaudeReportedError(message, envelope.get("api_error_status"))
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
