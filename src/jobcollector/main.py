"""Entry point: run the stages in order.

Implemented so far: collect, dedupe, filter and score. Run from the repo root.
"""

import argparse
import logging
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from jobcollector import filters, mailbox, scorer
from jobcollector.models import Job
from jobcollector.settings import load_config, load_env
from jobcollector.sources import adzuna, email_alerts
from jobcollector.store import Store, open_store

logger = logging.getLogger(__name__)


@dataclass
class Collected:
    jobs: list[Job] = field(default_factory=list)
    parsed_uids: list[str] = field(default_factory=list)
    failed: bool = False


def main(argv: Sequence[str] | None = None, root: Path = Path(".")) -> int:
    """Run once; return 1 if any part failed, after finishing the rest."""
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    env = load_env(root / ".env")
    config = load_config(root / "config.yaml")
    rules = filters.Rules.from_config(config)  # reject a bad config before doing any work
    settings = scorer.Settings.from_config(config)
    profile = (root / "profile.md").read_text(encoding="utf-8")

    collected = _collect(env, config)
    with open_store(root / "data" / "jobs.sqlite3", dry_run=args.dry_run) as store:
        new = store.add_new(collected.jobs)
        if not args.dry_run and not _mark_read(env, collected.parsed_uids):
            collected.failed = True
        # Includes jobs left unscored by earlier runs.
        kept, dropped = filters.apply(store.unscored(), rules)
        print(f"{len(collected.jobs)} collected, {len(new)} new, {len(kept)} to score")
        to_score = kept[: args.limit]
        if args.dry_run:
            _print_jobs(to_score)
            print("\nDropped:")
            for job, reason in dropped:
                print(f"  {job.title} | {job.company} ({reason})")
        elif not _score(store, to_score, profile, settings):
            collected.failed = True
    return 1 if collected.failed else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jobcollector")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print postings to score; do not score, mark emails as read or write the database",
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


def _score(
    store: Store, jobs: Sequence[Job], profile: str, settings: scorer.Settings
) -> bool:
    """Score and cache the jobs; return whether every one was scored."""
    scored = scorer.score_jobs(
        jobs, profile, settings, lambda scores: store.save_scores(scores, settings.model)
    )
    print(f"{scored} of {len(jobs)} scored")
    cached = store.scores()
    results = [(cached[job.job_id], job) for job in jobs if job.job_id in cached]
    for result, job in sorted(results, key=lambda pair: -pair[0].score):
        print(f"\n{result.score:3d} {job.title} | {job.company} | {job.location}\n    {job.url}")
        print(f"    + {'; '.join(result.reasons)}")
        if result.red_flags:
            print(f"    ! {'; '.join(result.red_flags)}")
    return scored == len(jobs)


def _collect(env: Mapping[str, str], config: Mapping[str, Any]) -> Collected:
    collected = Collected()
    for source in (_collect_emails, _collect_adzuna):
        try:
            source(env, config, collected)
        except Exception as error:  # a failing source must not abort the others
            collected.failed = True
            logger.error("%s failed: %s: %s", source.__name__, type(error).__name__, error)
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
    for message in messages:
        try:
            jobs = email_alerts.parse_email(message.raw)
        except Exception as error:  # one bad email must not lose the rest
            collected.failed = True
            logger.error("Could not parse an email: %s", type(error).__name__)
            continue
        # An alert that yields nothing stays unread, so a fixed parser retries it.
        if jobs:
            collected.jobs.extend(jobs)
            collected.parsed_uids.append(message.uid)


def _collect_adzuna(
    env: Mapping[str, str], config: Mapping[str, Any], collected: Collected
) -> None:
    options = config.get("adzuna") or {}
    collected.jobs.extend(
        adzuna.collect(
            env["ADZUNA_APP_ID"],
            env["ADZUNA_APP_KEY"],
            config.get("keywords") or [],
            config.get("locations") or [],
            max_days_old=int(options.get("max_days_old", 3)),
            max_pages=int(options.get("max_pages", 1)),
        )
    )


def _mark_read(env: Mapping[str, str], uids: Sequence[str]) -> bool:
    """Mark saved alerts as read. On failure the next run dedupes them."""
    if not uids:
        return True
    try:
        mailbox.mark_read(env["GMAIL_USER"], env["GMAIL_APP_PASSWORD"], uids)
    except Exception as error:
        logger.error("Could not mark emails as read: %s", type(error).__name__)
        return False
    return True


def _print_jobs(jobs: Sequence[Job]) -> None:
    for job in jobs:
        print(f"\n[{job.source}] {job.title}\n  {job.company} | {job.location}\n  {job.url}")


if __name__ == "__main__":
    # A redirected Windows console may not encode every title.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    sys.exit(main())
