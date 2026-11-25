import logging
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from jobcollector.models import Job
from jobcollector.sources import adzuna

RESULT = {
    "id": "4912345678",
    "title": "Senior <strong>Data Engineer</strong> &amp; Analyst",
    "company": {"display_name": "Acme"},
    "location": {"display_name": "Toronto, Ontario"},
    "description": "Build  pipelines, p < 5",
    "redirect_url": "https://www.adzuna.ca/land/ad/4912345678?se=x&utm_source=id",
}


class failures:
    """The failed-query count from the latest run()."""

    value = 0


def run(monkeypatch: pytest.MonkeyPatch, reply: Any, **kwargs: Any) -> tuple[list[Job], list[str]]:
    urls: list[str] = []

    def fake(url: str) -> Any:
        urls.append(url)
        return reply(url) if callable(reply) else reply

    monkeypatch.setattr(adzuna, "_get_json", fake)
    args: dict[str, Any] = dict(
        keywords=["data engineer"], locations=["Canada"], max_days_old=3, max_pages=1
    )
    jobs, failures.value = adzuna.collect("the-id", "the-key", **{**args, **kwargs})
    return jobs, urls


def fail(url: str) -> Any:
    raise HTTPError(url, 401, "Unauthorized", None, None)  # type: ignore[arg-type]


def test_parses_posting(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs, _ = run(monkeypatch, {"results": [RESULT]})
    assert jobs == [
        Job(
            "adzuna",
            "Senior Data Engineer & Analyst",
            "Acme",
            "Toronto, Ontario",
            "https://www.adzuna.ca/details/4912345678",
            "4912345678",
            "Build pipelines, p < 5",
        )
    ]


def test_queries_canada_for_each_keyword_and_location(monkeypatch: pytest.MonkeyPatch) -> None:
    _, urls = run(
        monkeypatch, {"results": []}, keywords=["a b", "c"], locations=["Canada", "Toronto"]
    )
    assert {urlsplit(u).path for u in urls} == {"/v1/api/jobs/ca/search/1"}
    queries = [parse_qs(urlsplit(u).query) for u in urls]
    assert [(q["what_phrase"], q.get("where")) for q in queries] == [
        (["a b"], None),
        (["a b"], ["Toronto"]),
        (["c"], None),
        (["c"], ["Toronto"]),
    ]
    assert queries[0]["app_id"] == ["the-id"] and queries[0]["app_key"] == ["the-key"]


def test_pages_until_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(url: str) -> Any:
        return {"results": [RESULT] * (50 if "/search/1?" in url else 2)}

    jobs, urls = run(monkeypatch, reply, max_pages=5)
    assert (len(jobs), len(urls)) == (52, 2)


def test_missing_company_kept_and_malformed_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    no_company = {"id": 1, "title": "Dev"}
    jobs, _ = run(monkeypatch, {"results": [no_company, {"title": "no id"}, "junk"]})
    assert [(j.external_id, j.company) for j in jobs] == [("1", "")]


def test_one_failed_query_keeps_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    def reply(url: str) -> Any:
        return fail(url) if "what_phrase=bad" in url else {"results": [RESULT]}

    jobs, _ = run(monkeypatch, reply, keywords=["bad", "good"])
    assert (len(jobs), failures.value) == (1, 1)


def test_all_failed_raises_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG), pytest.raises(adzuna.AdzunaError) as error:
        run(monkeypatch, fail)
    assert "401" in caplog.text
    assert "the-key" not in caplog.text + str(error.value)
