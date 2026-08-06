"""
Unit tests for the worker's collision-safe synthetic UID resolution.

``stable_uid`` derives a 30-bit hash which can collide once an archive grows
large; ``resolve_provider_uid`` claims each provider message's uid in the
``email_uid_aliases`` table so a collision can never make two distinct messages
share (source, folder, uid) in the emails table (which would silently overwrite
one of them). The database layer is mocked; no network or database required.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.exc import IntegrityError

WORKER_SRC = Path(__file__).resolve().parent.parent / "worker" / "src"
sys.path.insert(0, str(WORKER_SRC))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())

import worker  # noqa: E402


class _NoRow:
    def mappings(self):
        return self

    def first(self):
        return None


class _Row:
    def __init__(self, data):
        self._data = data

    def mappings(self):
        return self

    def first(self):
        return dict(self._data)


def _fake_query(returned_row):
    def fake_query(sql, params=None):
        return returned_row

    return fake_query


# ---------------------------------------------------------------------------
# stable_uid
# ---------------------------------------------------------------------------


def test_stable_uid_is_deterministic_and_in_range():
    uid1 = worker.stable_uid("17f3abc123")
    uid2 = worker.stable_uid("17f3abc123")
    assert uid1 == uid2
    assert 0 <= uid1 < 10**9  # fits the legacy INTEGER range


def test_stable_uid_distinguishes_messages():
    assert worker.stable_uid("msg-a") != worker.stable_uid("msg-b")


# ---------------------------------------------------------------------------
# resolve_provider_uid
# ---------------------------------------------------------------------------


def test_primary_uid_claimed_when_free(monkeypatch):
    """A free primary hash is used directly and its mapping is persisted."""
    primary = worker.stable_uid("msg-1")
    claimed = {}
    state = {"alias_uid": None}

    def fake_execute(sql, params=None):
        claimed["params"] = params
        state["alias_uid"] = params["uid"]  # claim persisted

    def fake_query(sql, params=None):
        # After a successful claim the alias row is visible to the re-read
        if state["alias_uid"] is None:
            return _NoRow()
        return _Row({"uid": state["alias_uid"]})

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "query", fake_query)

    uid = worker.resolve_provider_uid("account", "INBOX", "msg-1")
    assert uid == primary
    assert claimed["params"]["provider_id"] == "msg-1"
    assert claimed["params"]["uid"] == primary


def test_existing_alias_is_honoured(monkeypatch):
    """A previously persisted mapping (e.g. a resolved collision) wins."""
    monkeypatch.setattr(worker, "execute", lambda sql, params=None: None)
    monkeypatch.setattr(worker, "query", _fake_query(_Row({"uid": 987654})))

    assert worker.resolve_provider_uid("account", "INBOX", "msg-1") == 987654


def test_collision_uses_next_free_uid(monkeypatch):
    """When the primary uid is owned by a different message, the next free uid
    is claimed and remembered."""
    primary = worker.stable_uid("msg-collide")
    next_uid = primary + 1 if primary < 2_147_483_646 else 1
    claimed = []

    def fake_execute(sql, params=None):
        if "email_uid_aliases" not in sql:
            return  # e.g. log_error writes to the logs table
        uid = params["uid"]
        if uid == primary:
            raise IntegrityError("stmt", {}, Exception("duplicate key"))
        claimed.append(uid)

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "query", _fake_query(_NoRow()))

    uid = worker.resolve_provider_uid("account", "INBOX", "msg-collide")
    assert uid == next_uid
    assert claimed == [next_uid]


def test_log_error_never_raises(monkeypatch):
    """Logging is best-effort: a DB outage must not kill the worker loop."""

    def boom(sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(worker, "execute", boom)
    worker.log_error("Test", "something failed")  # must not raise


def test_transient_db_error_propagates(monkeypatch):
    """A non-integrity database error must propagate so the batch is retried
    instead of silently skipping the message."""

    def fake_execute(sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "query", _fake_query(_NoRow()))

    with pytest.raises(RuntimeError):
        worker.resolve_provider_uid("account", "INBOX", "msg-1")


# ---------------------------------------------------------------------------
# IMAP folder quoting
# ---------------------------------------------------------------------------


def test_quote_imap_folder_plain_and_special():
    assert worker._quote_imap_folder("INBOX") == "INBOX"
    assert worker._quote_imap_folder("My Folder") == '"My Folder"'
    assert worker._quote_imap_folder('Say "Hi"') == r'"Say \"Hi\""'
