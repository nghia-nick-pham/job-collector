import re
from pathlib import Path
from typing import Any

import pytest

from jobcollector import main as app
from jobcollector.claude_cli import ClaudeReportedError
from jobcollector.mailbox import RawMessage
from jobcollector.models import Job

FIXTURE = Path(__file__).parent / "fixtures" / "linkedin_1.eml"
ADZUNA_JOB = Job("adzuna", "Data Engineer", "Acme", "Ottawa", "https://a/1", "1")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / ".env").write_text(
        "GMAIL_USER=u\nGMAIL_APP_PASSWORD=p\nADZUNA_APP_ID=i\nADZUNA_APP_KEY=k\n"
    )
    (tmp_path / "config.yaml").write_text(
        "keywords: [data engineer]\nrubric: r\n"
        "scoring: {model: haiku, batch_size: 4, timeout_seconds: 9}\n"
    )
    (tmp_path / "profile.md").write_text("profile")
    return tmp_path


@pytest.fixture
def marked(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake both sources; return the UIDs passed to each mark_read call."""
    calls: list[list[str]] = []
    mail = [RawMessage("7", FIXTURE.read_bytes()), RawMessage("8", b"From: x@y.z\n\nhi")]
    monkeypatch.setattr(app.mailbox, "fetch_unread", lambda *a: mail)
    monkeypatch.setattr(app.mailbox, "mark_read", lambda u, p, uids: calls.append(list(uids)))
    monkeypatch.setattr(app.adzuna, "collect", lambda *a, **k: [ADZUNA_JOB])
    monkeypatch.setattr(app.scorer, "run_claude", fake_claude)
    return calls


def fake_claude(prompt: str, *args: Any) -> dict[str, Any]:
    ids = [int(i) for i in re.findall(r'"job_id": (\d+)', prompt)]
    return {
        "results": [
            {"job_id": i, "score": i, "stack_match": 1, "reasons": ["fits"], "red_flags": []}
            for i in ids
        ]
    }


def boom(*args: Any, **kwargs: Any) -> Any:
    raise OSError("down")


def test_dry_run_prints_and_changes_nothing(
    root: Path, marked: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert app.main(["--dry-run"], root) == 0
    out = capsys.readouterr().out
    assert "11 collected, 10 new" in out
    assert "https://www.linkedin.com/jobs/view/4492835541/" in out and "https://a/1" in out
    assert marked == []
    assert not (root / "data").exists()


def test_run_saves_then_marks_only_parsed_emails(
    root: Path, marked: list[list[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert app.main([], root) == 0
    assert marked == [["7"]]
    assert app.main([], root) == 0
    assert "11 collected, 0 new" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("module", "name", "expected"),
    [("mailbox", "fetch_unread", "1 collected"), ("adzuna", "collect", "10 collected")],
)
def test_failed_source_does_not_stop_the_other(
    root: Path,
    marked: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    module: str,
    name: str,
    expected: str,
) -> None:
    monkeypatch.setattr(getattr(app, module), name, boom)
    assert app.main([], root) == 1
    assert expected in capsys.readouterr().out


def test_nothing_marked_when_save_fails(
    root: Path, marked: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app, "open_store", boom)
    with pytest.raises(OSError):
        app.main([], root)
    assert marked == []


def test_mark_read_failure_is_reported(
    root: Path, marked: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.mailbox, "mark_read", boom)
    assert app.main([], root) == 1


def cached_ids(root: Path) -> list[int]:
    with app.open_store(root / "data" / "jobs.sqlite3") as store:
        return sorted(store.scores())


def test_limit_scores_newest_and_next_run_scores_only_the_rest(
    root: Path,
    marked: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert app.main(["--limit", "3"], root) == 0
    assert cached_ids(root) == [8, 9, 10]
    assert app.main([], root) == 0
    assert cached_ids(root) == list(range(1, 11))
    assert "7 of 7 scored" in capsys.readouterr().out
    monkeypatch.setattr(app.scorer, "run_claude", boom)  # everything is cached
    assert app.main([], root) == 0


def test_limit_zero_and_dry_run_never_call_claude(
    root: Path,
    marked: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(app.scorer, "run_claude", boom)
    assert app.main(["--dry-run", "--limit", "2"], root) == 0
    assert capsys.readouterr().out.count("  https://") == 2
    assert app.main(["--limit", "0"], root) == 0


def test_claude_failure_still_saves_jobs_and_retries_next_run(
    root: Path, marked: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    def limited(*args: Any) -> Any:
        raise ClaudeReportedError("usage limit")

    monkeypatch.setattr(app.scorer, "run_claude", limited)
    assert app.main([], root) == 1
    assert (marked, cached_ids(root)) == ([["7"]], [])
    monkeypatch.setattr(app.scorer, "run_claude", fake_claude)
    assert app.main([], root) == 0
    assert len(cached_ids(root)) == 10


@pytest.mark.parametrize("value", ["-1", "x"])
def test_bad_limit_is_rejected(root: Path, value: str) -> None:
    with pytest.raises(SystemExit):
        app.main(["--limit", value], root)
