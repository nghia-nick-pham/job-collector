from pathlib import Path
from typing import Any

import pytest

from jobcollector.filters import Rules, apply
from jobcollector.models import Job
from jobcollector.settings import load_config

RULES = Rules(
    include_any=("data engineer", "software engineer", "python", "c++"),
    exclude_title_any=("director", "principal", "intern"),
    exclude_any=("us citizen", "security clearance"),
    locations_ok=("canada", "remote", "toronto", "ontario"),
)


def reason(
    title: str = "Data Engineer",
    description: str = "",
    location: str = "Toronto, ON",
    source: str = "linkedin",
    rules: Rules = RULES,
) -> str | None:
    job = Job(source, title, "Acme", location, "https://x", "1", description)
    kept, dropped = apply([job], rules)
    assert len(kept) + len(dropped) == 1
    return dropped[0][1] if dropped else None


@pytest.mark.parametrize(
    "title",
    [
        "Senior Data Engineer",
        "SENIOR DATA ENGINEER",
        "Data-Engineer (Remote)",
        "Data  Engineers",
        "Staff Software Engineer, Directory Services",  # not "director"
        "Software Engineer, Internal Tools",  # not "intern"
        "Data Engineer - International Markets",
        "Python Developer",
        "Developer (C++)",
    ],
)
def test_title_kept(title: str) -> None:
    assert reason(title) is None


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Director of Data Engineering", "excluded title term: director"),
        ("Director, Data Engineer Programs", "excluded title term: director"),
        ("Principal Data Engineer", "excluded title term: principal"),
        ("Data Engineer Intern", "excluded title term: intern"),
        ("Software Engineer Interns", "excluded title term: intern"),
        ("Data Engineer (Security Clearance)", "excluded term: security clearance"),
        # "engineering" is not "engineer".
        ("Head of Data Engineering", "no included term"),
        ("Data Analyst", "no included term"),
        ("Database Engineer", "no included term"),
        ("C Developer", "no included term"),
    ],
)
def test_title_dropped(title: str, expected: str) -> None:
    assert reason(title) == expected


def test_title_exclusions_ignore_the_description() -> None:
    assert reason(description="Report to the Director; mentor interns.") is None


@pytest.mark.parametrize(
    "text", ["Must be a U.S. citizen", "US Citizens only", "must be U.S Citizen.", "us-citizen"]
)
def test_description_exclusions_ignore_punctuation(text: str) -> None:
    assert reason(description=text) == "excluded term: us citizen"


@pytest.mark.parametrize("text", ["Help us. Citizen services team", "plus citizen science"])
def test_us_citizen_near_misses(text: str) -> None:
    # Punctuation is ignored, so only the second is a true near miss.
    expected = "excluded term: us citizen" if text.startswith("Help") else None
    assert reason(description=text) == expected


def test_include_can_match_the_description() -> None:
    assert reason("Developer", description="Build pipelines in Python.") is None


def test_exclusion_is_reported_before_missing_include_or_location() -> None:
    assert reason("Director of Sales", location="Paris") == "excluded title term: director"


@pytest.mark.parametrize(
    "location",
    [
        "Toronto, ON",
        "Mississauga, ON",
        "Vancouver, BC",
        "Toronto, ON M5V 2T6",
        "Toronto, ON (Hybrid)",
        "Ottawa, Ontario",
        "Alberta",
        "Greater Toronto Area, Canada",
        "Remote",
        "",
    ],
)
def test_location_kept(location: str) -> None:
    assert reason(location=location) is None


@pytest.mark.parametrize("location", ["Austin, TX", "Paris, France", "On site, Chicago, IL"])
def test_location_dropped(location: str) -> None:
    assert reason(location=location) == f"location: {location}"


def test_province_code_satisfies_only_its_own_province() -> None:
    rules = Rules(locations_ok=("ontario",))
    assert reason(location="London, ON", rules=rules) is None
    assert reason(location="Vancouver, BC", rules=rules) == "location: Vancouver, BC"


def test_adzuna_locations_are_canadian() -> None:
    assert reason(location="Calgary, Calgary region", source="adzuna") is None
    assert reason(location="Calgary, Calgary region") is not None


def test_empty_rules_keep_everything_and_order_is_preserved() -> None:
    jobs = [Job("s", t, "c", "l", "u", t) for t in ("b", "a", "c")]
    assert apply(jobs, Rules()) == (jobs, [])
    kept, dropped = apply(jobs, Rules(exclude_title_any=("a",)))
    assert kept == [jobs[0], jobs[2]]
    assert dropped == [(jobs[1], "excluded title term: a")]


@pytest.mark.parametrize("value", ["python", ["python", 3], [""], ["!"], {"a": 1}])
def test_bad_config_is_rejected(value: Any) -> None:
    with pytest.raises(ValueError, match="include_any"):
        Rules.from_config({"include_any": value})


def test_missing_or_null_config_means_no_rule() -> None:
    assert Rules.from_config({"include_any": None}) == Rules()


def test_committed_config() -> None:
    config = load_config(Path(__file__).parents[1] / "config.yaml")
    rules = Rules.from_config(config)
    assert reason("Senior Data Engineer", rules=rules) is None
    assert reason("Director of Data Engineering", rules=rules) == "excluded title term: director"
