from dataclasses import replace
from pathlib import Path

from jobcollector.models import Job
from jobcollector.scorer import JobScore
from jobcollector.store import open_store

JOB = Job("indeed", "Data Engineer", "Acme", "Toronto, ON", "https://x/1", "1")
NO_ID = replace(JOB, external_id=None, url="https://x/redirect")


def add(path: Path, *jobs: Job, dry_run: bool = False) -> list[Job]:
    with open_store(path, dry_run=dry_run) as store:
        return store.add_new(jobs)


def test_same_id_is_new_once_within_and_across_runs(tmp_path: Path) -> None:
    db = tmp_path / "data" / "j.db"
    retitled = replace(JOB, title="Other title")
    assert add(db, JOB, retitled) == [JOB]
    assert add(db, JOB) == []


def test_two_ids_from_one_source_are_distinct(tmp_path: Path) -> None:
    other = replace(JOB, external_id="2")
    assert add(tmp_path / "j.db", JOB, other) == [JOB, other]


def test_cross_source_match_ignores_case_and_province(tmp_path: Path) -> None:
    other = Job("adzuna", "data  engineer", "ACME", "Toronto, Ontario", "https://y", "9")
    assert add(tmp_path / "j.db", JOB, other) == [JOB]


def test_different_city_or_punctuated_title_is_distinct(tmp_path: Path) -> None:
    jobs = [
        replace(JOB, source=f"s{i}", title=title, location=city)
        for i, (title, city) in enumerate(
            [("C Dev", "Toronto"), ("C++ Dev", "Toronto"), ("C Dev", "Ottawa")]
        )
    ]
    assert add(tmp_path / "j.db", *jobs) == jobs


def test_job_without_id_dedupes_on_content(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    assert add(db, NO_ID) == [NO_ID]
    assert add(db, replace(NO_ID, url="https://x/redirect2")) == []


def test_id_completes_earlier_job_without_one(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    second = replace(JOB, external_id="2")
    assert add(db, NO_ID) == [NO_ID]
    assert add(db, JOB) == []
    assert add(db, second) == [second]


def test_jobs_without_company_never_match_on_content(tmp_path: Path) -> None:
    a = replace(JOB, company="")
    b = replace(a, source="adzuna")
    assert add(tmp_path / "j.db", a, b) == [a, b]


def test_dry_run_sees_existing_jobs_but_writes_nothing(tmp_path: Path) -> None:
    db = tmp_path / "data" / "j.db"
    assert add(db, JOB, dry_run=True) == [JOB]
    assert not db.parent.exists()
    add(db, JOB)
    other = replace(JOB, external_id="2")
    assert add(db, JOB, other, dry_run=True) == [other]
    assert add(db, other) == [other]


def test_scores_are_cached_by_job_id_and_survive_reopening(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    second = replace(JOB, external_id="2")
    add(db, JOB, second)
    score = JobScore(job_id=1, score=80, stack_match=30, reasons=["é"], red_flags=[])
    with open_store(db) as store:
        assert [job.job_id for job in store.unscored()] == [2, 1]
        store.save_scores([score], "haiku")
    with open_store(db) as store:
        assert store.unscored() == [replace(second, job_id=2)]
        assert store.scores() == {1: score}


def test_score_survives_when_a_job_gains_an_id(tmp_path: Path) -> None:
    db = tmp_path / "j.db"
    add(db, NO_ID)
    with open_store(db) as store:
        store.save_scores(
            [JobScore(job_id=1, score=1, stack_match=1, reasons=[], red_flags=[])], "haiku"
        )
        assert store.add_new([JOB]) == []
        assert store.unscored() == []
