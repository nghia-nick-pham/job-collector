"""The Job dataclass shared by every stage."""

from dataclasses import dataclass


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
