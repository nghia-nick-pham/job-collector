from datetime import date
from typing import Any

import pytest

from jobcollector import mailbox


class FakeImap:
    def __init__(self, status: str = "OK") -> None:
        self.status = status
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, host: str, timeout: int) -> "FakeImap":
        return self

    def __enter__(self) -> "FakeImap":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def select(self, box: str, readonly: bool) -> tuple[str, list]:
        self.calls.append(("select", readonly))
        return "OK", [b"1"]

    def uid(self, *args: str) -> tuple[str, list]:
        self.calls.append(args)
        if args[0] == "SEARCH":
            return self.status, [b"7 8"]
        return self.status, [
            (f"{n} (UID {uid} BODY[] {{3}}".encode(), b"raw" + uid.encode())
            for n, uid in enumerate(args[1].split(","), 1)
        ] + [b")"]


def use(monkeypatch: pytest.MonkeyPatch, fake: FakeImap) -> FakeImap:
    monkeypatch.setattr(mailbox.imaplib, "IMAP4_SSL", fake)
    return fake


def test_fetch_never_marks_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, FakeImap())
    messages = mailbox.fetch_unread("u", "p", ["a@b.c"], date(2026, 3, 5))
    assert messages == [mailbox.RawMessage("7", b"raw7"), mailbox.RawMessage("8", b"raw8")]
    assert fake.calls[0] == ("select", True)
    assert fake.calls[1] == ("SEARCH", "UNSEEN", "FROM", '"a@b.c"', "SINCE", "05-Mar-2026")
    assert fake.calls[2] == ("FETCH", "7,8", "(BODY.PEEK[])")


def test_mark_read_sets_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use(monkeypatch, FakeImap())
    mailbox.mark_read("u", "p", ["7", "8"])
    assert fake.calls == [("select", False), ("STORE", "7,8", "+FLAGS.SILENT", r"(\Seen)")]


def test_refused_command_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, FakeImap(status="NO"))
    with pytest.raises(mailbox.MailboxError):
        mailbox.mark_read("u", "p", ["7"])
