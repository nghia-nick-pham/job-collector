import re
from pathlib import Path
from typing import Any

import pytest

from jobcollector import main as app
from jobcollector.claude_cli import ClaudeReportedError
from jobcollector.digest import Digest
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
    monkeypatch.setattr(app.adzuna, "collect", lambda *a, **k: ([ADZUNA_JOB], 0))
    monkeypatch.setattr(app.scorer, "run_claude", fake_claude)
    monkeypatch.setattr(app.digest, "send", lambda message, user, password: None)
    return calls


@pytest.fixture
def sent(marked: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> list[Digest]:
    messages: list[Digest] = []
    monkeypatch.setattr(app.digest, "send", lambda message, u, p: messages.append(message))
    return messages


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
    with app.open_store(root / "data" / "jobs.db") as store:
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


def test_digest_is_sent_once_per_job(root: Path, sent: list[Digest]) -> None:
    app.main([], root)
    app.main([], root)
    assert [len(message.job_ids) for message in sent] == [10, 0]
    assert "LinkedIn: 10 postings; 1 unread alerts" in sent[0].html
    assert "Indeed: 0 postings; 0 unread alerts" in sent[0].html
    assert "Adzuna: 1 postings" in sent[0].html
    assert "10 of 10 jobs scored; 0 batches failed." in sent[0].html


def test_failed_send_is_retried_next_run(
    root: Path, sent: list[Digest], monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(app.digest, "send", boom)
        assert app.main([], root) == 1
    assert app.main([], root) == 0
    assert len(sent[0].job_ids) == 10


def test_preview_writes_file_and_leaves_jobs_unemailed(root: Path, sent: list[Digest]) -> None:
    assert app.main(["--preview"], root) == 0
    assert sent == []
    assert "Adzuna: 1 postings" in (root / "digest.html").read_text(encoding="utf-8")
    app.main([], root)
    assert len(sent[0].job_ids) == 10


def test_dry_run_preview_changes_nothing_else(
    root: Path, sent: list[Digest], marked: list[list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app.scorer, "run_claude", boom)
    assert app.main(["--preview", "--dry-run"], root) == 0
    assert "Scoring did not run." in (root / "digest.html").read_text(encoding="utf-8")
    assert (sent, marked, (root / "data").exists()) == ([], [], False)


def test_dry_run_neither_sends_nor_writes_digest(root: Path, sent: list[Digest]) -> None:
    app.main(["--dry-run"], root)
    assert sent == [] and not (root / "digest.html").exists()


def test_digest_reports_failures(
    root: Path, sent: list[Digest], monkeypatch: pytest.MonkeyPatch
) -> None:
    def logged_out(*args: Any) -> Any:
        raise ClaudeReportedError("Not logged in \u00b7 Please run /login")

    monkeypatch.setattr(app.scorer, "run_claude", logged_out)
    monkeypatch.setattr(app.adzuna, "collect", boom)
    assert app.main([], root) == 1
    assert "Adzuna: 0 postings; OSError: down" in sent[0].html
    assert "0 of 9 jobs scored; 1 batches failed; 2 batches not attempted; reason: auth." in (
        sent[0].html
    )


def test_unreadable_alert_is_a_source_problem(
    root: Path, sent: list[Digest], monkeypatch: pytest.MonkeyPatch
) -> None:
    mail = [RawMessage("9", b"From: donotreply@jobalert.indeed.com\n\nnew layout")]
    monkeypatch.setattr(app.mailbox, "fetch_unread", lambda *a: mail)
    assert app.main([], root) == 1
    assert "Indeed: 0 postings; 1 unread alerts, 1 with no readable postings" in sent[0].html
