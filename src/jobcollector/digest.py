"""Stage 5: render and send the email digest."""

import smtplib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import Any
from urllib.parse import urlsplit

from jobcollector.models import Job, ScoringHealth, SourceHealth
from jobcollector.scorer import JobScore

_HOST = "smtp.gmail.com"
_PORT = 465
_TIMEOUT_SECONDS = 60

_FONT = "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
_CARD = (
    "background-color:#ffffff;border:1px solid #e2e5ea;border-radius:8px;"
    "padding:14px 16px;margin:0 0 10px;"
)
_HEADING = "font-size:18px;line-height:24px;margin:24px 0 10px;color:#1c2430;"
_SMALL = "font-size:14px;line-height:20px;margin:4px 0 0;color:#4a5462;"
_LINK = "color:#0b57d0;text-decoration:none;font-weight:600;"
_GREEN, _AMBER, _GREY, _RED = "#137333", "#9a5b00", "#5f6368", "#b3261e"


@dataclass(frozen=True)
class Settings:
    strong: int = 80
    good: int = 60
    maybe: int = 40
    max_jobs: int = 50

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "Settings":
        """Read the settings; raise ValueError if one is invalid."""
        options = config.get("digest") or {}
        names = cls.__dataclass_fields__
        settings = cls(**{name: options[name] for name in names if name in options})
        values = [getattr(settings, name) for name in names]
        if any(type(value) is not int for value in values):
            raise ValueError("config.yaml: digest values must be whole numbers")
        if not 100 >= settings.strong > settings.good > settings.maybe > 0:
            raise ValueError("config.yaml: digest needs 100 >= strong > good > maybe > 0")
        if settings.max_jobs < 1:
            raise ValueError("config.yaml: digest.max_jobs must be at least 1")
        return settings


@dataclass(frozen=True)
class Digest:
    subject: str
    html: str
    text: str
    job_ids: list[int]  # the postings included, to mark as emailed


def build(
    scored: Sequence[tuple[Job, JobScore]],
    sources: Sequence[SourceHealth],
    scoring: ScoringHealth | None,
    settings: Settings,
) -> Digest:
    """Render the best postings; any over max_jobs wait for the next digest.

    Gmail cuts off long messages, which is why there is a limit.
    """
    ranked = sorted(scored, key=lambda pair: -pair[1].score)
    shown = ranked[: settings.max_jobs]
    waiting = len(ranked) - len(shown)
    tiers = _tiers(shown, settings)
    strong = len(tiers[0][2])
    healthy = all(source.ok for source in sources) and (scoring is None or scoring.ok)
    warning = "" if healthy else " (problems)"
    subject = f"Job digest: {len(shown)} new, {strong} strong{warning}"
    return Digest(
        subject=subject,
        html=_html(subject, tiers, waiting, sources, scoring),
        text=_text(tiers, waiting, sources, scoring),
        job_ids=[score.job_id for _, score in shown],
    )


def send(digest: Digest, user: str, password: str) -> None:
    """Send the digest from the Gmail account to itself."""
    message = EmailMessage()
    message["Subject"] = digest.subject
    message["From"] = user
    message["To"] = user
    message.set_content(digest.text)
    message.add_alternative(digest.html, subtype="html")
    with smtplib.SMTP_SSL(_HOST, _PORT, timeout=_TIMEOUT_SECONDS) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)


_Tier = tuple[str, str, list[tuple[Job, JobScore]]]


def _tiers(shown: Sequence[tuple[Job, JobScore]], settings: Settings) -> list[_Tier]:
    """Return (heading, color, postings) per tier, best first."""
    bounds = [
        ("Strong match", _GREEN, settings.strong, 101),
        ("Good match", "#0b57d0", settings.good, settings.strong),
        ("Maybe", _AMBER, settings.maybe, settings.good),
        ("Low match", _GREY, 0, settings.maybe),
    ]
    return [
        (name, color, [pair for pair in shown if low <= pair[1].score < high])
        for name, color, low, high in bounds
    ]


def _html(
    subject: str,
    tiers: Sequence[_Tier],
    waiting: int,
    sources: Sequence[SourceHealth],
    scoring: ScoringHealth | None,
) -> str:
    sections: list[str] = []
    for index, (name, color, pairs) in enumerate(tiers):
        if not pairs:
            continue
        sections.append(
            f'<h2 style="{_HEADING}border-left:4px solid {color};padding-left:10px;">'
            f"{name} ({len(pairs)})</h2>"
        )
        compact = index == len(tiers) - 1
        sections += [_compact(*pair) if compact else _card(*pair, color) for pair in pairs]
    if not sections:
        sections.append(f'<p style="{_SMALL}">No new scored postings.</p>')
    if waiting:
        sections.append(f'<p style="{_SMALL}">{waiting} more will be in the next digest.</p>')
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        f"<title>{escape(subject)}</title></head>"
        f'<body style="margin:0;padding:0;background-color:#f3f5f8;{_FONT}">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        '<tr><td align="center" style="padding:12px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"'
        ' style="max-width:600px;"><tr><td style="text-align:left;">'
        '<h1 style="font-size:22px;line-height:28px;margin:8px 0 0;color:#1c2430;">'
        f"{escape(subject)}</h1>"
        f"{''.join(sections)}{_health(sources, scoring)}"
        "</td></tr></table></td></tr></table></body></html>\n"
    )


def _card(job: Job, score: JobScore, color: str) -> str:
    lines = [
        f'<div style="{_CARD}">'
        f'<span style="display:inline-block;background-color:{color};color:#ffffff;'
        'border-radius:12px;padding:1px 10px;font-size:14px;line-height:22px;'
        f'font-weight:700;">{score.score}</span>'
        '<div style="font-size:17px;line-height:24px;margin:6px 0 0;word-break:break-word;">'
        f"{_title(job)}</div>"
        f'<p style="{_SMALL}">{_place(job)}</p>'
    ]
    if score.reasons:
        lines.append(f'<p style="{_SMALL}">{escape("; ".join(score.reasons))}</p>')
    if score.red_flags:
        flags = escape("; ".join(score.red_flags))
        lines.append(f'<p style="{_SMALL}color:{_RED};">Red flags: {flags}</p>')
    lines.append(
        f'<p style="{_SMALL}color:{_GREY};">Stack {score.stack_match}/40 &middot; '
        f"{escape(job.source)}</p></div>"
    )
    return "".join(lines)


def _compact(job: Job, score: JobScore) -> str:
    return f'<p style="{_SMALL}word-break:break-word;">{score.score} {_title(job)}, {_place(job)}</p>'


def _title(job: Job) -> str:
    title = escape(job.title)
    if urlsplit(job.url).scheme not in ("https", "http"):
        return title
    return f'<a href="{escape(job.url)}" style="{_LINK}">{title}</a>'


def _place(job: Job) -> str:
    return escape(" | ".join(text for text in (job.company, job.location) if text))


def _health(sources: Sequence[SourceHealth], scoring: ScoringHealth | None) -> str:
    rows = "".join(
        f'<p style="{_SMALL}"><b style="color:{_status_color(source.ok)};">'
        f"{_status(source.ok)}</b> {escape(_source_line(source))}</p>"
        for source in sources
    )
    ok = scoring is None or scoring.ok
    return (
        f'<h2 style="{_HEADING}">Source health</h2><div style="{_CARD}">{rows}</div>'
        f'<h2 style="{_HEADING}">Scoring health</h2><div style="{_CARD}">'
        f'<p style="{_SMALL}"><b style="color:{_status_color(ok)};">{_status(ok)}</b> '
        f"{escape(_scoring_line(scoring))}</p></div>"
    )


def _status(ok: bool) -> str:
    return "OK" if ok else "PROBLEM"


def _status_color(ok: bool) -> str:
    return _GREEN if ok else _RED


def _source_line(source: SourceHealth) -> str:
    return f"{source.name}: {source.jobs} postings" + (f"; {source.detail}" if source.detail else "")


def _scoring_line(scoring: ScoringHealth | None) -> str:
    if scoring is None:
        return "Scoring did not run."
    line = (
        f"{scoring.scored} of {scoring.requested} jobs scored; "
        f"{scoring.batches_failed} batches failed"
    )
    if scoring.batches_not_run:
        line += f"; {scoring.batches_not_run} batches not attempted"
    if scoring.reasons:
        line += f"; reason: {', '.join(dict.fromkeys(scoring.reasons))}"
    return line + "."


def _text(
    tiers: Sequence[_Tier],
    waiting: int,
    sources: Sequence[SourceHealth],
    scoring: ScoringHealth | None,
) -> str:
    lines: list[str] = []
    for name, _, pairs in tiers:
        if pairs:
            lines += [f"{name} ({len(pairs)})", ""]
            for job, score in pairs:
                lines += [f"{score.score} {job.title}", f"{job.company} | {job.location}"]
                lines += [job.url, ""]
    if not lines:
        lines += ["No new scored postings.", ""]
    if waiting:
        lines += [f"{waiting} more will be in the next digest.", ""]
    lines.append("Source health")
    lines += [f"{_status(source.ok)} {_source_line(source)}" for source in sources]
    lines += ["", "Scoring health", _scoring_line(scoring)]
    return "\n".join(lines) + "\n"
