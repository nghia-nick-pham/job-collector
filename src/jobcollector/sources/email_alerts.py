"""Collect postings from job-alert emails.

Both parsers read the text/plain part, where each posting is a few lines
ending in its link.
"""

import email
import logging
import re
from collections.abc import Callable
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import parse_qs, urlsplit

from jobcollector.models import Job

logger = logging.getLogger(__name__)

_LINKEDIN_LINK = "View job:"
# The heading can sit directly above the first card, with no blank line.
_LINKEDIN_BREAKS = ("-----", "Your job alert for ")
_LINKEDIN_PATH = re.compile(r"/(?:comm/)?jobs/view/(?:[^/]*-)?(\d+)/?")
_INDEED_KEY = re.compile(r"[0-9a-f]{16}")
_INDEED_JOB_PATHS = ("/rc/clk", "/pagead/clk", "/viewjob")
_INDEED_REDIRECT_HOST = "engage.indeed.com"


def parse_email(raw: bytes) -> list[Job]:
    """Parse a raw message with the parser for its sender."""
    message = email.message_from_bytes(raw, policy=policy.default)
    sender = _sender(message)
    parser = _PARSERS.get(sender)
    if parser is None:
        logger.info("No parser for this sender; message ignored")
        return []
    jobs = parser(_plain_text(message))
    if not jobs:
        logger.warning("No postings found in an alert from %s", sender)
    return jobs


def sender_of(raw: bytes) -> str:
    """Return the sender's bare address in lower case."""
    return _sender(email.message_from_bytes(raw, policy=policy.default))


def parse_linkedin(text: str) -> list[Job]:
    """Parse the plain text of a LinkedIn job alert.

    A card is title, company and location, then optional badges, possibly
    after a blank line. Links and dashed rules end a section. Headings form
    shorter paragraphs than cards, so the card is the first paragraph of at
    least three lines in its section.
    """
    jobs: list[Job] = []
    section: list[str] = []
    for line in map(str.strip, text.splitlines()):
        if line.startswith(_LINKEDIN_LINK):
            job = _linkedin_job(section, line.removeprefix(_LINKEDIN_LINK).strip())
            if job is not None:
                jobs.append(job)
            section = []
        elif "https://" in line or line.startswith(_LINKEDIN_BREAKS):
            section = []
        else:
            section.append(line)
    return jobs


def parse_indeed(text: str) -> list[Job]:
    """Parse the plain text of an Indeed job alert.

    A card is one paragraph: title, "company - location", details, link.
    """
    jobs: list[Job] = []
    for lines in _paragraphs(text.splitlines()):
        if len(lines) < 3 or not lines[-1].startswith("https://"):
            continue
        job = _indeed_job(lines)
        if job is not None:
            jobs.append(job)
    return jobs


def _linkedin_job(section: list[str], url: str) -> Job | None:
    posting_id = _linkedin_id(url)
    card = next((p for p in _paragraphs(section) if len(p) >= 3), None)
    if posting_id is None or card is None:
        logger.warning("Skipped a LinkedIn posting that could not be parsed")
        return None
    title, company, location = card[:3]
    return Job(
        source="linkedin",
        title=title,
        company=company,
        location=location,
        url=f"https://www.linkedin.com/jobs/view/{posting_id}/",
        external_id=posting_id,
    )


def _linkedin_id(url: str) -> str | None:
    host, path, _ = _split(url)
    match = _LINKEDIN_PATH.fullmatch(path)
    return match[1] if match and _in_domain(host, "linkedin.com") else None


def _indeed_job(lines: list[str]) -> Job | None:
    link = _indeed_link(lines[-1])
    # rpartition: company names contain " - " more often than locations do.
    company, separator, location = lines[1].rpartition(" - ")
    if link is None or not separator:
        logger.warning("Skipped an Indeed posting that could not be parsed")
        return None
    url, job_key = link
    return Job(
        source="indeed",
        title=lines[0],
        company=company.strip(),
        location=location.strip(),
        url=url,
        external_id=job_key,
    )


def _indeed_link(url: str) -> tuple[str, str | None] | None:
    """Return (url, job key), or None if this is not an Indeed job link.

    Redirect and sponsored links carry no job key and stop working if
    trimmed, so they are returned unchanged with a key of None.
    """
    host, path, query = _split(url)
    if host == _INDEED_REDIRECT_HOST:
        return url, None
    if not _in_domain(host, "indeed.com") or not path.startswith(_INDEED_JOB_PATHS):
        return None
    job_key = parse_qs(query).get("jk", [""])[0]
    if _INDEED_KEY.fullmatch(job_key):
        return f"https://{host}/viewjob?jk={job_key}", job_key
    return url, None


def _split(url: str) -> tuple[str, str, str]:
    """Return (host, path, query), all empty if the URL is malformed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "", "", ""
    return parts.hostname or "", parts.path, parts.query


def _in_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _paragraphs(lines: list[str]) -> list[list[str]]:
    """Group stripped lines into runs separated by blank lines."""
    groups: list[list[str]] = [[]]
    for line in map(str.strip, lines):
        if line:
            groups[-1].append(line)
        elif groups[-1]:
            groups.append([])
    return [group for group in groups if group]


def _sender(message: EmailMessage) -> str:
    return parseaddr(str(message["From"] or ""))[1].lower()


def _plain_text(message: EmailMessage) -> str:
    body = message.get_body(preferencelist=("plain",))
    return str(body.get_content()) if body is not None else ""


_PARSERS: dict[str, Callable[[str], list[Job]]] = {
    "jobalerts-noreply@linkedin.com": parse_linkedin,
    "donotreply@jobalert.indeed.com": parse_indeed,
}
# Address to display name.
SENDERS: dict[str, str] = {
    "jobalerts-noreply@linkedin.com": "LinkedIn",
    "donotreply@jobalert.indeed.com": "Indeed",
}
