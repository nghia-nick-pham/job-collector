from email.message import EmailMessage
from typing import Any

import pytest

from jobcollector import digest
from jobcollector.digest import Settings, build
from jobcollector.models import Job, ScoringHealth, SourceHealth
from jobcollector.scorer import JobScore

OK_SOURCE = SourceHealth("LinkedIn", 12, True, "3 unread alerts")
OK_SCORING = ScoringHealth(requested=20, scored=20, batches_failed=0, batches_not_run=0)


def pair(job_id: int, score: int, **fields: Any) -> tuple[Job, JobScore]:
    values = {"title": f"Job-{job_id}.", "company": "Acme", "location": "Toronto", "url": "https://x"}
    job = Job(source="indeed", job_id=job_id, **{**values, **fields})
    return job, JobScore(
        job_id=job_id, score=score, stack_match=30, reasons=["good fit"], red_flags=[]
    )


def make(*pairs: tuple[Job, JobScore], **kwargs: Any) -> digest.Digest:
    args = {"sources": [OK_SOURCE], "scoring": OK_SCORING, "settings": Settings(), **kwargs}
    return build(pairs, **args)


def test_groups_by_tier_at_the_boundaries() -> None:
    scores = [100, 80, 79, 60, 59, 40, 39, 0]
    result = make(*(pair(i, s) for i, s in enumerate(scores)))
    for name in ("Strong match", "Good match", "Maybe", "Low match"):
        assert f"{name} (2)" in result.html
    positions = [result.html.index(f"Job-{i}.") for i in range(8)]
    assert positions == sorted(positions)
    assert result.subject == "Job digest: 8 new, 2 strong"
    assert sorted(result.job_ids) == list(range(8))


def test_empty_tiers_are_left_out_and_no_jobs_is_stated() -> None:
    assert "Maybe" not in make(pair(1, 90)).html
    result = make()
    assert "No new scored postings." in result.html and "No new scored postings." in result.text
    assert result.job_ids == []


def test_limit_keeps_the_best_and_reports_the_rest() -> None:
    result = make(pair(1, 50), pair(2, 90), pair(3, 70), settings=Settings(max_jobs=2))
    assert result.job_ids == [2, 3]
    assert "1 more will be in the next digest." in result.html


def test_untrusted_text_is_escaped() -> None:
    job, score = pair(1, 90, title="<script>x</script>", url='https://x/?a=1&b="><img>')
    score = score.model_copy(update={"reasons": ["<b>r"], "red_flags": ["<i>f"]})
    html = make((job, score), sources=[SourceHealth("<u>s", 1, False, "<u>d")]).html
    for bad in ("<script>", "<img>", "<b>r", "<i>f", "<u>"):
        assert bad not in html
    assert "Red flags: &lt;i&gt;f" in html
    assert 'href="https://x/?a=1&amp;b=&quot;&gt;' in html


def test_unsafe_link_is_not_linked() -> None:
    assert "javascript:" not in make(pair(1, 90, url="javascript:alert(1)")).html


def test_layout_is_mobile_friendly_with_inline_css_only() -> None:
    html = make(pair(1, 90)).html
    assert 'name="viewport"' in html and "max-width:600px" in html
    assert "<style" not in html and "<link" not in html and "<img" not in html


def test_full_digest_fits_gmail_clipping_limit() -> None:
    url = "https://engage.indeed.com/f/a/" + "x" * 700
    result = make(*(pair(i, 90, url=url, title="T" * 80) for i in range(60)))
    assert len(result.html.encode()) < 102_000


def test_source_health_shows_failures_and_flags_subject() -> None:
    failed = SourceHealth("Adzuna", 0, False, "2 queries failed")
    result = make(sources=[OK_SOURCE, failed])
    assert "LinkedIn: 12 postings; 3 unread alerts" in result.html
    assert "Adzuna: 0 postings; 2 queries failed" in result.html
    assert "PROBLEM" in result.html
    assert result.subject.endswith("(problems)")
    assert "PROBLEM" not in make().html


@pytest.mark.parametrize("reason", ["auth", "usage limit", "timeout"])
def test_scoring_health_line(reason: str) -> None:
    scoring = ScoringHealth(30, 10, 2, 1, [reason, reason])
    result = make(scoring=scoring)
    line = (
        "10 of 30 jobs scored; 2 batches failed; 1 batches not attempted; "
        f"reason: {reason}."
    )
    assert line in result.html and line in result.text
    assert result.subject.endswith("(problems)")


def test_scoring_not_run() -> None:
    assert "Scoring did not run." in make(scoring=None).html


@pytest.mark.parametrize(
    "options", [{"strong": 50}, {"maybe": 0}, {"good": "60"}, {"max_jobs": 0}, {"strong": 101}]
)
def test_bad_settings_are_rejected(options: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="digest"):
        Settings.from_config({"digest": options})


def test_settings_default_and_override() -> None:
    assert Settings.from_config({}) == Settings()
    assert Settings.from_config({"digest": {"strong": 85}}) == Settings(strong=85)


def test_send_logs_in_over_tls_and_mails_the_account_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    class FakeSmtp:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            calls.append((host, port))

        def __enter__(self) -> "FakeSmtp":
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def login(self, user: str, password: str) -> None:
            calls.append((user, password))

        def send_message(self, message: EmailMessage) -> None:
            calls.append(message)

    monkeypatch.setattr(digest.smtplib, "SMTP_SSL", FakeSmtp)
    digest.send(make(pair(1, 90, title="Développeur")), "me@example.com", "pw")
    message = calls[2]
    assert calls[:2] == [("smtp.gmail.com", 465), ("me@example.com", "pw")]
    assert message["From"] == message["To"] == "me@example.com"
    for kind in ("html", "plain"):
        assert "Développeur" in message.get_body((kind,)).get_content()
