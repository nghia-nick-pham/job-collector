"""Tests for the job-alert email parsers, against scrubbed real emails."""

import logging
from email.message import EmailMessage
from pathlib import Path

import pytest

from jobcollector.models import Job
from jobcollector.sources.email_alerts import parse_email, parse_indeed, parse_linkedin

FIXTURES = Path(__file__).parent / "fixtures"

LI = "https://www.linkedin.com/jobs/view/{}/"
IN = "https://ca.indeed.com/viewjob?jk={}"

Row = tuple[str, str, str, str | None]


def load(name: str) -> list[Job]:
    return parse_email((FIXTURES / name).read_bytes())


def rows(jobs: list[Job]) -> list[Row]:
    return [(j.title, j.company, j.location, j.external_id) for j in jobs]


def make_email(sender: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["Subject"] = "alert"
    msg.set_content(body)
    return msg.as_bytes()


LINKEDIN_1: list[Row] = [
    ("Java/J2EE Developer", "Enterprise Solutions Inc.", "Mississauga, ON", "4492835541"),
    ("Java Developer", "Lorven Technologies Inc.", "Mississauga, ON", "4492612658"),
    ("Java Backend Developer", "Tata Consultancy Services", "Toronto, ON", "4494051380"),
    ("Software Engineer - Java", "Haventree Bank", "Toronto, ON", "4434013017"),
    ("Java Technical Lead", "Carbonsoft", "Greater Toronto Area, Canada", "4492634978"),
    ("Java Developer", "Staffingine LLC", "Mississauga, ON", "4492630001"),
    # "Other alerts" sections; the first repeats a posting (store.py dedupes).
    ("Java/J2EE Developer", "Enterprise Solutions Inc.", "Mississauga, ON", "4492835541"),
    ("Backend / API Engineer, Radar", "Stripe", "Toronto, ON", "4482036292"),
    ("Full Stack Engineer, Link", "Stripe", "Toronto, ON", "4481790006"),
    ("Senior Full-Stack Engineer, NimbleRx", "Swoop", "Toronto, ON", "4491198511"),
]

LINKEDIN_2: list[Row] = [
    ("Java Backend Developer", "Enterprise Solutions Inc.", "Mississauga, ON", "4492835541"),
    ("Senior Java Developer", "CGI", "Toronto, ON", "4448487756"),
    ("Staff Software Engineer", "ecobee", "Toronto, ON", "4425381199"),
    ("Senior Java Engineer", "RE Partners", "Mississauga, ON", "4492351960"),
    ("Backend / API Engineer, Radar", "Stripe", "Toronto, ON", "4482036292"),
    ("Senior Software Engineer (Back-end/Server)", "Trulioo", "Toronto, ON", "4469226035"),
    ("Senior Java Developer", "CGI", "Toronto, ON", "4448487756"),
    ("Middle Full-Stack Engineer (React, Java)", "Rolejob", "Toronto, ON", "4493512672"),
    ("Software Engineer II, Fullstack (Ads Platform)", "Affirm", "Windsor, ON", "4476871705"),
]

# Every link in this email is an opaque redirect, so no posting has an id.
INDEED_1: list[Row] = [
    ("Junior (Java) Developer", "Citi", "Mississauga, ON", None),
    ("Junior Developer (Java)", "Citi", "Mississauga, ON", None),
    ("Drupal Developer", "Universal Health Hub", "Markham, ON", None),
    ("Software Developer", "Fidelity Investments", "Mississauga, ON", None),
    ("Backend Engineer", "US Mobile", "Toronto, ON", None),
    ("Senior Software Engineer [OPERATIONS TECHNOLOGY]", "HelloFresh", "Toronto, ON", None),
    ("Guidewire Developer", "Clear Prism", "Toronto, ON", None),
    ("Process Control & Automation Engineer", "Ionada Canada Corporation", "Markham, ON", None),
    ("AWS Cloud Engineer", "Dacaro Software Services Inc", "Toronto, ON", None),
    ("Machine Learning Engineer, Computer Vision", "LoopX", "Markham, ON", None),
]

INDEED_2: list[Row] = [
    ("Java Full-Stack Developer", "Capgemini", "Toronto, ON", "735ef7e04ed38cf5"),
    ("Associate Java Full-Stack Developer", "Priceline.com", "Toronto, ON", "8291ec340b6343fd"),
    ("Full Stack Developer - Digital Platform", "Air Canada", "Toronto, ON", "01b7fdac16cc0328"),
    ("Java Developer", "Capgemini", "Toronto, ON", "024e2fb18288b336"),
    ("Senior Fullstack Developer", "CIBC", "Toronto, ON", "2554ef016f028f54"),
    ("Frontier Engineer – Java, React & Node.js", "Cognizant", "Toronto, ON", "4fae2cf0901f87e3"),
    (
        "Systems Developer (AI Focus)",
        "Municipal Property Assessment Corporation",
        "Pickering, ON",
        "e06841711c63b83c",
    ),
    (
        "IT Manager Development (Java/Spring Boot/Kafka)",
        "TD Bank",
        "Toronto, ON",
        "c9638b87fe063d0b",
    ),
    ("Consultant - Application Developer", "CIBC", "Toronto, ON", "056d59c07c57c5cf"),
    ("Frontier Engineer – Java, React & Node.js", "Cognizant", "Toronto, ON", "0c406696562556c0"),
    ("Backend Engineer", "US Mobile", "Toronto, ON", "dee41a36c166cbad"),
    ("Guidewire Developer", "Clear Prism", "Toronto, ON", None),  # sponsored link
    ("Staff Backend Engineer", "US Mobile", "Toronto, ON", "73e96ad73dfe7435"),
    ("Senior Java Developer", "Cognizant", "Toronto, ON", "e0ce0a18bcdf4e08"),
    (
        "Software Development Student (4 Months) - Winter 2027",
        "Canadian Tire Corporation",
        "Toronto, ON",
        "9a41218130a9c144",
    ),
    ("Software Engineer II", "Samsara", "Toronto, ON", "f4577cec0dae3f4c"),
    ("Senior Software Engineer", "Ripple", "Toronto, ON", "2dd29af6be5af3cd"),
]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("linkedin_1.eml", LINKEDIN_1),
        ("linkedin_2.eml", LINKEDIN_2),
        ("indeed_1.eml", INDEED_1),
        ("indeed_2.eml", INDEED_2),
    ],
)
def test_extracts_every_posting_in_order(name: str, expected: list[Row]) -> None:
    assert rows(load(name)) == expected


@pytest.mark.parametrize(
    ("name", "source", "template"),
    [
        ("linkedin_1.eml", "linkedin", LI),
        ("linkedin_2.eml", "linkedin", LI),
        ("indeed_2.eml", "indeed", IN),
    ],
)
def test_urls_are_canonical(name: str, source: str, template: str) -> None:
    jobs = load(name)
    assert {j.source for j in jobs} == {source}
    with_id = [j for j in jobs if j.external_id is not None]
    assert with_id
    for job in with_id:
        assert job.url == template.format(job.external_id)


def test_indeed_redirects_without_job_key_keep_working_link() -> None:
    urls = [j.url for j in load("indeed_1.eml")]
    assert all(u.startswith("https://engage.indeed.com/f/a/") for u in urls)
    assert len(set(urls)) == len(urls)


def test_indeed_sponsored_link_is_kept_whole() -> None:
    [job] = [j for j in load("indeed_2.eml") if j.external_id is None]
    assert job.url.startswith("https://ca.indeed.com/pagead/clk/dl?mo=")


def test_indeed_company_containing_dash_splits_on_last_separator() -> None:
    body = (
        "Dev\nAcme - Labs - Toronto, ON\n1 day ago\n"
        "https://ca.indeed.com/rc/clk/dl?jk=0123456789abcdef&tk=x\n"
    )
    assert rows(parse_indeed(body)) == [("Dev", "Acme - Labs", "Toronto, ON", "0123456789abcdef")]


def test_linkedin_badges_after_blank_line_are_not_the_card() -> None:
    body = (
        "Dev\nAcme\nToronto, ON\n\nActively hiring\nTop applicant\nEasy apply\n"
        "View job: https://www.linkedin.com/comm/jobs/view/123/?trk=x\n"
    )
    assert rows(parse_linkedin(body)) == [("Dev", "Acme", "Toronto, ON", "123")]


@pytest.mark.parametrize(
    ("sender", "body"),
    [
        (
            "jobalerts-noreply@linkedin.com",
            "Dev\nAcme\nToronto\nView job: https://linkedin.com.evil.test/comm/jobs/view/1/\n",
        ),
        (
            "donotreply@jobalert.indeed.com",
            "Dev\nAcme - Toronto\nNew\nhttps://notindeed.com/rc/clk/dl?jk=0123456789abcdef\n",
        ),
    ],
)
def test_links_to_other_hosts_are_skipped(sender: str, body: str) -> None:
    assert parse_email(make_email(sender, body)) == []


@pytest.mark.parametrize("sender", ["x@example.com", "LinkedIn <a@linkedin.com.evil.test>"])
def test_unknown_sender_yields_nothing(sender: str) -> None:
    body = "Dev\nAcme\nToronto\nView job: https://www.linkedin.com/comm/jobs/view/1/\n"
    assert parse_email(make_email(sender, body)) == []


def test_malformed_card_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    link = "View job: https://www.linkedin.com/comm/jobs/view/{}/\n"
    body = "Only a title\n" + link.format(1) + "\nDev\nAcme\nToronto\n" + link.format(2)
    with caplog.at_level(logging.WARNING):
        jobs = parse_linkedin(body)
    assert [j.external_id for j in jobs] == ["2"]
    assert caplog.records


def test_known_sender_without_postings_warns(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert parse_email(make_email("donotreply@jobalert.indeed.com", "Hello\n")) == []
    assert caplog.records
