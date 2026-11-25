"""Read alert emails from Gmail over IMAP; the only module that uses imaplib."""

import imaplib
import re
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date

_HOST = "imap.gmail.com"
_TIMEOUT_SECONDS = 60
_BATCH_SIZE = 25  # one round trip per message is slow with a backlog
_UID = re.compile(rb"\bUID (\d+)")
# IMAP needs English month names; strftime("%b") depends on the locale.
_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class MailboxError(Exception):
    """The server refused a command."""


@dataclass(frozen=True)
class RawMessage:
    uid: str
    raw: bytes


def fetch_unread(
    user: str, password: str, senders: Iterable[str], since: date
) -> list[RawMessage]:
    """Return unread messages from the senders, leaving them unread."""
    messages: list[RawMessage] = []
    with _inbox(user, password, readonly=True) as imap:
        for sender in senders:
            criteria = ("UNSEEN", "FROM", f'"{sender}"', "SINCE", _imap_date(since))
            found = _check(imap.uid("SEARCH", *criteria))
            uids = (found[0] or b"").decode().split()
            for start in range(0, len(uids), _BATCH_SIZE):
                batch = ",".join(uids[start : start + _BATCH_SIZE])
                messages.extend(_messages(_check(imap.uid("FETCH", batch, "(BODY.PEEK[])"))))
    return messages


def mark_read(user: str, password: str, uids: Sequence[str]) -> None:
    if not uids:
        return
    with _inbox(user, password, readonly=False) as imap:
        _check(imap.uid("STORE", ",".join(uids), "+FLAGS.SILENT", r"(\Seen)"))


@contextmanager
def _inbox(user: str, password: str, readonly: bool) -> Iterator[imaplib.IMAP4_SSL]:
    with imaplib.IMAP4_SSL(_HOST, timeout=_TIMEOUT_SECONDS) as imap:
        imap.login(user, password)
        _check(imap.select("INBOX", readonly=readonly))
        yield imap


def _messages(parts: list) -> list[RawMessage]:
    """Pair each body in a FETCH response with the UID the server reports for it."""
    messages: list[RawMessage] = []
    for part in parts:
        if not isinstance(part, tuple):
            continue
        match = _UID.search(part[0])
        if match is None:
            raise MailboxError("FETCH response without a UID")
        messages.append(RawMessage(match[1].decode(), part[1]))
    return messages


def _check(response: tuple[str, list]) -> list:
    status, data = response
    if status != "OK":
        raise MailboxError(f"IMAP command failed: {status}")
    return data


def _imap_date(day: date) -> str:
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"
