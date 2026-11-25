import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from jobcollector import claude_cli
from jobcollector.claude_cli import run_claude

SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}}}


class FakeRun:
    """Stands in for subprocess.run and records the call."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.reply = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.error: Exception | None = None
        self.command: list[str] = []
        self.kwargs: dict[str, Any] = {}
        self.files: list[str] | None = None

    def __call__(self, command: list[str], **kwargs: Any) -> Any:
        self.command, self.kwargs = command, kwargs
        self.files = os.listdir(kwargs["cwd"])
        if self.error is not None:
            raise self.error
        return self.reply


def install(monkeypatch: pytest.MonkeyPatch, fake: FakeRun) -> FakeRun:
    monkeypatch.setattr(claude_cli.subprocess, "run", fake)
    return fake


def call(prompt: str = "hi") -> dict[str, Any]:
    return run_claude(prompt, SCHEMA, "haiku", 30)


def envelope(**fields: Any) -> str:
    return json.dumps({"type": "result", "is_error": False, **fields})


def test_command_and_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = install(monkeypatch, FakeRun(envelope(structured_output={})))
    prompt = 'Évaluer "$(whoami)" & del *'
    call(prompt)
    assert fake.command == [
        "claude",
        "-p",
        "--model",
        "haiku",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(SCHEMA),
        "--tools",
        "",
        "--restricted",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]
    assert fake.kwargs["input"] == prompt
    assert fake.kwargs["encoding"] == "utf-8"
    assert fake.kwargs["timeout"] == 30
    assert not fake.kwargs.get("shell")
    assert "env" not in fake.kwargs


def test_runs_in_fresh_empty_directory_removed_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = install(monkeypatch, FakeRun(envelope(structured_output={})))
    call()
    first = Path(fake.kwargs["cwd"])
    assert fake.files == []
    assert not first.exists()
    call()
    assert Path(fake.kwargs["cwd"]) != first


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"structured_output": {"score": 7}, "result": "ignored"}, {"score": 7}),
        ({"structured_output": {}, "result": '{"score": 1}'}, {}),
        ({"structured_output": None, "result": '{"score": 5}'}, {"score": 5}),
        ({"result": '{"score": 5}'}, {"score": 5}),
    ],
)
def test_returns_answer(
    monkeypatch: pytest.MonkeyPatch, fields: dict[str, Any], expected: dict[str, Any]
) -> None:
    install(monkeypatch, FakeRun(envelope(**fields)))
    assert call() == expected


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "[]",
        envelope(),
        envelope(result=None),
        envelope(result="plain text"),
        envelope(result="[1]"),
        envelope(structured_output=[1]),
    ],
)
def test_unparseable_output(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    install(monkeypatch, FakeRun(stdout))
    with pytest.raises(claude_cli.ClaudeOutputError):
        call()


def test_nonzero_exit_includes_stderr_and_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeRun("usage limit", returncode=3, stderr="bad flag\n"))
    with pytest.raises(claude_cli.ClaudeExitError, match="code 3: bad flag . usage limit") as info:
        call()
    assert (info.value.returncode, info.value.stderr) == (3, "bad flag\n")


def test_reported_error(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, FakeRun(envelope(is_error=True, result="limit reached")))
    with pytest.raises(claude_cli.ClaudeReportedError, match="limit reached"):
        call()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (subprocess.TimeoutExpired("claude", 30), claude_cli.ClaudeTimeoutError),
        (FileNotFoundError("claude"), claude_cli.ClaudeNotFoundError),
    ],
)
def test_process_errors(
    monkeypatch: pytest.MonkeyPatch, error: Exception, expected: type[Exception]
) -> None:
    fake = install(monkeypatch, FakeRun())
    fake.error = error
    with pytest.raises(expected) as info:
        call()
    assert isinstance(info.value, claude_cli.ClaudeError)
    assert not Path(fake.kwargs["cwd"]).exists()
