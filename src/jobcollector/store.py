"""SQLite dedupe, score cache and emailed state; the only module that contains SQL.

Emailed state is not implemented yet.
"""

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from jobcollector.models import Job
from jobcollector.scorer import JobScore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    fingerprint TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    url TEXT NOT NULL,
    description TEXT NOT NULL,
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS jobs_fingerprint ON jobs (fingerprint);
CREATE TABLE IF NOT EXISTS scores (
    job_id INTEGER PRIMARY KEY REFERENCES jobs (id),
    score INTEGER NOT NULL,
    stack_match INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    red_flags TEXT NOT NULL,
    model TEXT NOT NULL,
    scored_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._db = connection
        self._db.executescript(_SCHEMA)

    def add_new(self, jobs: Iterable[Job]) -> list[Job]:
        """Save the jobs not seen before, in one transaction, and return them."""
        with self._db:
            return [job for job in jobs if self._add(job)]

    def unscored(self) -> list[Job]:
        """Return saved jobs without a score, newest first."""
        rows = self._db.execute(
            "SELECT source, title, company, location, url, external_id, description, id"
            " FROM jobs WHERE id NOT IN (SELECT job_id FROM scores) ORDER BY id DESC"
        )
        return [Job(*row) for row in rows]

    def save_scores(self, scores: Iterable[JobScore], model: str) -> None:
        """Cache the scores in one transaction."""
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO scores"
                " (job_id, score, stack_match, reasons, red_flags, model)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        score.job_id,
                        score.score,
                        score.stack_match,
                        json.dumps(score.reasons),
                        json.dumps(score.red_flags),
                        model,
                    )
                    for score in scores
                ],
            )

    def scores(self) -> dict[int, JobScore]:
        """Return every cached score by job id."""
        rows = self._db.execute(
            "SELECT job_id, score, stack_match, reasons, red_flags FROM scores"
        )
        return {
            row[0]: JobScore(
                job_id=row[0],
                score=row[1],
                stack_match=row[2],
                reasons=json.loads(row[3]),
                red_flags=json.loads(row[4]),
            )
            for row in rows
        }

    def _add(self, job: Job) -> bool:
        fingerprint = _fingerprint(job)
        if job.external_id is not None and self._has_id(job):
            return False
        if fingerprint is not None:
            if job.external_id is not None and self._give_id(job, fingerprint):
                return False
            if self._matches(job, fingerprint):
                return False
        self._db.execute(
            "INSERT INTO jobs (source, external_id, fingerprint, title, company,"
            " location, url, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.source,
                job.external_id,
                fingerprint,
                job.title,
                job.company,
                job.location,
                job.url,
                job.description,
            ),
        )
        return True

    def _has_id(self, job: Job) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM jobs WHERE source = ? AND external_id = ?",
            (job.source, job.external_id),
        ).fetchone()
        return row is not None

    def _give_id(self, job: Job, fingerprint: str) -> bool:
        """Complete a posting saved earlier from a link without an id."""
        cursor = self._db.execute(
            "UPDATE jobs SET external_id = ?, url = ? WHERE id = ("
            "SELECT id FROM jobs WHERE fingerprint = ? AND source = ?"
            " AND external_id IS NULL LIMIT 1)",
            (job.external_id, job.url, fingerprint, job.source),
        )
        return cursor.rowcount > 0

    def _matches(self, job: Job, fingerprint: str) -> bool:
        """Match on content. Two ids from one source are two postings."""
        row = self._db.execute(
            "SELECT 1 FROM jobs WHERE fingerprint = ? AND (? IS NULL OR source <> ?)",
            (fingerprint, job.external_id, job.source),
        ).fetchone()
        return row is not None


@contextmanager
def open_store(path: Path, dry_run: bool = False) -> Iterator[Store]:
    """Open the database. A dry run works on an in-memory copy of it."""
    if dry_run:
        connection = sqlite3.connect(":memory:")
        if path.exists():
            _copy(path, connection)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    try:
        yield Store(connection)
    finally:
        connection.close()


def _copy(path: Path, target: sqlite3.Connection) -> None:
    source = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        source.backup(target)
    finally:
        source.close()


def _fingerprint(job: Job) -> str | None:
    """Return title, company and city, or None if any is missing.

    Only the city is compared because sources write provinces differently.
    """
    city = job.location.partition(",")[0]
    parts = [" ".join(text.casefold().split()) for text in (job.title, job.company, city)]
    return "\n".join(parts) if all(parts) else None
