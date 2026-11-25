"""Collect postings from the Adzuna API."""

import html
import json
import logging
import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from jobcollector.models import Job

logger = logging.getLogger(__name__)

_COUNTRY = "ca"
_SEARCH_URL = f"https://api.adzuna.com/v1/api/jobs/{_COUNTRY}/search"
_PAGE_SIZE = 50
_TIMEOUT_SECONDS = 30
_TAG = re.compile(r"</?\w+[^<>]*>")


class AdzunaError(Exception):
    """Every query failed."""


def collect(
    app_id: str,
    app_key: str,
    keywords: Sequence[str],
    locations: Sequence[str],
    max_days_old: int,
    max_pages: int,
) -> list[Job]:
    """Search each keyword in each location; one failed query skips only itself.

    Overlapping results are returned as is; store.py dedupes.
    """
    jobs: list[Job] = []
    queries = [(k, place) for k in keywords for place in (locations or ["Canada"])]
    failures = 0
    for keyword, location in queries:
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "what_phrase": keyword,
            "max_days_old": str(max_days_old),
            "sort_by": "date",
            "results_per_page": str(_PAGE_SIZE),
        }
        if location.casefold() != "canada":
            params["where"] = location
        try:
            for page in range(1, max_pages + 1):
                results = _fetch_page(page, params)
                jobs.extend(job for r in results if (job := _parse(r)) is not None)
                if len(results) < _PAGE_SIZE:
                    break
        except (OSError, ValueError) as error:
            # Never log the URL: it contains the API key.
            failures += 1
            status = getattr(error, "code", "")
            logger.error("Adzuna query %r failed: %s %s", keyword, type(error).__name__, status)
    if queries and failures == len(queries):
        raise AdzunaError("all Adzuna queries failed")
    return jobs


def _fetch_page(page: int, params: dict[str, str]) -> list[dict[str, Any]]:
    payload = _get_json(f"{_SEARCH_URL}/{page}?{urlencode(params)}")
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise ValueError("unexpected Adzuna response")
    return results


def _get_json(url: str) -> Any:
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        return json.load(response)


def _parse(result: dict[str, Any]) -> Job | None:
    try:
        posting_id = str(result["id"])
        title = _clean(result["title"])
        # Adzuna omits the company on some postings.
        company = _clean((result.get("company") or {}).get("display_name") or "")
        location = _clean((result.get("location") or {}).get("display_name") or "")
        description = _clean(result.get("description") or "")
    except (KeyError, TypeError, AttributeError):
        logger.warning("Skipped an Adzuna posting that could not be parsed")
        return None
    if not posting_id.isdigit() or not title:
        logger.warning("Skipped an Adzuna posting without an id or title")
        return None
    return Job(
        source="adzuna",
        title=title,
        company=company,
        location=location,
        url=f"https://www.adzuna.ca/details/{posting_id}",
        external_id=posting_id,
        description=description,
    )


def _clean(text: str) -> str:
    """Remove search-hit markup and collapse whitespace."""
    return " ".join(html.unescape(_TAG.sub("", text)).split())
