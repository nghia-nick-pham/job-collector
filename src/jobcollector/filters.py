"""Stage 3: cheap rule-based filtering before any LLM call."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from jobcollector.models import Job

_PROVINCES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}
_PROVINCE_NAMES = {name.casefold() for name in _PROVINCES.values()}
# Sources that only search Canada, though their locations may not say so.
_CANADIAN_SOURCES = {"adzuna"}

_ACRONYM_DOT = re.compile(r"\b([a-z])\.(?=[a-z]\b)")
_NOT_WORD = re.compile(r"[^\w+#]+")


@dataclass(frozen=True)
class Rules:
    include_any: tuple[str, ...] = ()
    exclude_title_any: tuple[str, ...] = ()
    exclude_any: tuple[str, ...] = ()
    locations_ok: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "Rules":
        """Read the rules; raise ValueError if one is not a list of terms."""
        return cls(**{name: _terms(config, name) for name in cls.__dataclass_fields__})


def apply(jobs: Iterable[Job], rules: Rules) -> tuple[list[Job], list[tuple[Job, str]]]:
    """Return (kept, dropped_with_reason), each in input order."""
    kept: list[Job] = []
    dropped: list[tuple[Job, str]] = []
    for job in jobs:
        reason = _drop_reason(job, rules)
        if reason is None:
            kept.append(job)
        else:
            dropped.append((job, reason))
    return kept, dropped


def _drop_reason(job: Job, rules: Rules) -> str | None:
    title = _normalize(job.title)
    description = _normalize(job.description)
    if term := _first_match(rules.exclude_title_any, title):
        return f"excluded title term: {term}"
    if term := _first_match(rules.exclude_any, title, description):
        return f"excluded term: {term}"
    if rules.include_any and not _first_match(rules.include_any, title, description):
        return "no included term"
    # An unknown location is left for the scorer to judge.
    if rules.locations_ok and job.location.strip():
        if not _first_match(rules.locations_ok, _normalize(_expand_location(job))):
            return f"location: {job.location}"
    return None


def _first_match(terms: Iterable[str], *texts: str) -> str | None:
    """Return the first term found as whole words in a normalized text."""
    for term in terms:
        pattern = rf"(?<!\S){re.escape(_normalize(term))}s?(?!\S)"
        if any(re.search(pattern, text) for text in texts):
            return term
    return None


def _normalize(text: str) -> str:
    """Casefold, join dotted acronyms (U.S. -> us), turn punctuation into spaces."""
    joined = _ACRONYM_DOT.sub(r"\1", text.casefold())
    return _NOT_WORD.sub(" ", joined).strip()


def _expand_location(job: Job) -> str:
    """Append the province name and "Canada" where the location implies them."""
    location = job.location
    extra: list[str] = ["Canada"] if job.source in _CANADIAN_SOURCES else []
    for index, part in enumerate(location.split(",")):
        words = part.split()
        if index > 0 and words and words[0] in _PROVINCES:
            extra += [_PROVINCES[words[0]], "Canada"]
        elif part.strip().casefold() in _PROVINCE_NAMES:
            extra.append("Canada")
    return ", ".join([location, *extra])


def _terms(config: Mapping[str, Any], name: str) -> tuple[str, ...]:
    value = config.get(name)
    if value is None:
        return ()
    # A bare string would otherwise be read as a list of letters.
    if not isinstance(value, list) or not all(
        isinstance(term, str) and _normalize(term) for term in value
    ):
        raise ValueError(f"config.yaml: {name} must be a list of non-empty strings")
    return tuple(value)
