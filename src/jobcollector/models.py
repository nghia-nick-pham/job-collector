"""The dataclasses shared by several stages."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Job:
    """One posting.

    `external_id` is the source's own posting id. When it is None, `url` is
    not canonical and can differ between alerts for the same posting, so it
    must not be used as a dedupe key.
    """

    source: str
    title: str
    company: str
    location: str
    url: str
    external_id: str | None = None
    description: str = ""
    job_id: int | None = None  # set by store.py once saved


@dataclass(frozen=True)
class SourceHealth:
    """How collecting from one source went."""

    name: str
    jobs: int
    ok: bool
    detail: str = ""


@dataclass
class ScoringHealth:
    """How scoring went. `reasons` has one entry per failed batch."""

    requested: int
    scored: int = 0
    batches_failed: int = 0
    batches_not_run: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.scored == self.requested
