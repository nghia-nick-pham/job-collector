"""Entry point: run the stages in order. Run from the repo root."""

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jobcollector import digest, filters, mailbox, scorer
from jobcollector.models import Job, ScoringHealth, SourceHealth
from jobcollector.settings import load_config, load_env
from jobcollector.sources import adzuna, email_alerts
from jobcollector.store import Store, open_store

logger = logging.getLogger(__name__)


@dataclass
class Collected:
    jobs: list[Job] = field(default_factory=list)
    parsed_uids: list[str] = field(default_factory=list)
    sources: list[SourceHealth] = field(default_factory=list)


def main(argv: Sequence[str] | None = None, root: Path = Path(".")) -> int:
    """Run once; return 1 if any part failed, after finishing the rest."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env = load_env(root / ".env")
    config = load_config(root / "config.yaml")
    # Reject a bad config before doing any work.
    rules = filters.Rules.from_config(config)
    settings = scorer.Settings.from_config(config)
    digest_settings = digest.Settings.from_config(config)
    profile = (root / "profile.md").read_text(encoding="utf-8")

    collected = _collect(env, config)
    scoring: ScoringHealth | None = None
    delivered = True
    with open_store(root / "data" / "jobs.db", dry_run=args.dry_run) as store:
        new = store.add_new(collected.jobs)
        if not args.dry_run:
            _mark_read(env, collected)
        # Includes jobs left unscored by earlier runs.
        kept, dropped = filters.apply(store.unscored(), rules)
        print(f"{len(collected.jobs)} collected, {len(new)} new, {len(kept)} to score")
        to_score = kept[: args.limit]
        if args.dry_run:
            _print_dry_run(to_score, dropped)
        else:
            scoring = scorer.score_jobs(
                to_score,
                profile,
                settings,
                lambda scores: store.save_scores(scores, settings.model),
            )
            print(f"{scoring.scored} of {scoring.requested} scored")
        if args.preview or not args.dry_run:
            message = digest.build(
                store.unemailed(), collected.sources, scoring, digest_settings
            )
            if args.preview:
                _write_preview(root / "digest.html", message)
            else:
                delivered = _send(env, store, message)
    healthy = all(source.ok for source in collected.sources)
    return 0 if healthy and delivered and (scoring is None or scoring.ok) else 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jobcollector")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print postings to score; do not score, send, mark emails as read"
        " or write the database",
    )
    parser.add_argument(
        "--preview",
        action="store_true",
        help="write digest.html instead of sending; other stages run as usual"
        " (add --dry-run to change nothing else)",
    )
    parser.add_argument(
        "--limit",
        type=_limit,
        metavar="N",
        help="score at most N postings, newest first (default: all)",
    )
    return parser.parse_args(argv)


def _limit(text: str) -> int:
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def _write_preview(path: Path, message: digest.Digest) -> None:
    path.write_text(message.html, encoding="utf-8")
    print(f"Wrote {path}: {message.subject}")


def _send(env: Mapping[str, str], store: Store, message: digest.Digest) -> bool:
    """Send the digest, then mark its postings as emailed."""
    try:
        digest.send(message, env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"])
    except Exception as error:
        logger.error("Could not send the digest: %s", _describe(error))
        return False
    store.mark_emailed(message.job_ids)
    print(f"Sent: {message.subject}")
    return True


def _collect(env: Mapping[str, str], config: Mapping[str, Any]) -> Collected:
    collected = Collected()
    for name, source in (("Email alerts", _collect_emails), ("Adzuna", _collect_adzuna)):
        try:
            source(env, config, collected)
        except Exception as error:  # a failing source must not abort the others
            logger.error("%s failed: %s", name, _describe(error))
            collected.sources.append(SourceHealth(name, 0, False, _describe(error)))
    return collected


def _collect_emails(
    env: Mapping[str, str], config: Mapping[str, Any], collected: Collected
) -> None:
    max_age = int((config.get("email") or {}).get("max_age_days", 14))
    messages = mailbox.fetch_unread(
        env["GMAIL_USER"],
        env["GMAIL_APP_PASSWORD"],
        email_alerts.SENDERS,
        date.today() - timedelta(days=max_age),
    )
    alerts = dict.fromkeys(email_alerts.SENDERS, 0)
    unusable = dict.fromkeys(email_alerts.SENDERS, 0)
    postings = dict.fromkeys(email_alerts.SENDERS, 0)
    for message in messages:
        sender = email_alerts.sender_of(message.raw)
        if sender not in alerts:
            continue
        alerts[sender] += 1
        try:
            jobs = email_alerts.parse_email(message.raw)
        except Exception as error:  # one bad email must not lose the rest
            logger.error("Could not parse an email: %s", type(error).__name__)
            jobs = []
        # An alert that yields nothing stays unread, so a fixed parser retries it.
        if jobs:
            collected.jobs.extend(jobs)
            collected.parsed_uids.append(message.uid)
            postings[sender] += len(jobs)
        else:
            unusable[sender] += 1
    for sender, name in email_alerts.SENDERS.items():
        detail = f"{alerts[sender]} unread alerts"
        if unusable[sender]:
            detail += f", {unusable[sender]} with no readable postings"
        collected.sources.append(
            SourceHealth(name, postings[sender], not unusable[sender], detail)
        )


def _collect_adzuna(
    env: Mapping[str, str], config: Mapping[str, Any], collected: Collected
) -> None:
    options = config.get("adzuna") or {}
    jobs, failed = adzuna.collect(
        env["ADZUNA_APP_ID"],
        env["ADZUNA_APP_KEY"],
        config.get("keywords") or [],
        config.get("locations") or [],
        max_days_old=int(options.get("max_days_old", 3)),
        max_pages=int(options.get("max_pages", 1)),
    )
    collected.jobs.extend(jobs)
    detail = f"{failed} queries failed" if failed else ""
    collected.sources.append(SourceHealth("Adzuna", len(jobs), not failed, detail))


def _mark_read(env: Mapping[str, str], collected: Collected) -> None:
    """Mark saved alerts as read. On failure the next run dedupes them."""
    if not collected.parsed_uids:
        return
    try:
        mailbox.mark_read(env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"], collected.parsed_uids)
    except Exception as error:
        logger.error("Could not mark emails as read: %s", _describe(error))
        detail = f"could not mark alerts as read: {_describe(error)}"
        collected.sources.append(SourceHealth("Gmail", 0, False, detail))


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error)[:200]}"


def _print_dry_run(jobs: Sequence[Job], dropped: Sequence[tuple[Job, str]]) -> None:
    for job in jobs:
        print(f"\n[{job.source}] {job.title}\n  {job.company} | {job.location}\n  {job.url}")
    print("\nDropped:")
    for job, reason in dropped:
        print(f"  {job.title} | {job.company} ({reason})")


if __name__ == "__main__":
    # A redirected Windows console may not encode every title.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    sys.exit(main())
