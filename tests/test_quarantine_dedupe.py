"""Regression tests for quarantine deduplication and retry behavior."""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet


ROOT = Path(__file__).resolve().parent.parent
API_DIR = ROOT / "api"
WORKER_SRC = ROOT / "worker" / "src"
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(WORKER_SRC))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

import worker  # noqa: E402
from src.routes import emails as emails_mod  # noqa: E402


RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: recipient@example.com\r\n"
    b"Subject: duplicate quarantine\r\n"
    b"Message-ID: <dedupe@example.com>\r\n"
    b"Date: Tue, 01 Jan 2030 00:00:00 +0000\r\n"
    b"\r\n"
    b"body\r\n"
)


class _VirusScanner:
    _quarantine_in_db = True
    _quarantine_encrypt = False
    _quarantine_key = None
    _quarantine_retention_days = 90

    def is_enabled(self):
        return True

    def requires_scan(self):
        return True

    def scan(self, _raw):
        return True, "Eicar-Test", datetime.now(timezone.utc), True

    def get_action(self):
        return "quarantine"


def test_worker_duplicate_quarantine_is_idempotent(monkeypatch):
    """A repeated scan of the same identity succeeds without a second row."""
    state = {"quarantine_keys": set(), "quarantine_attempts": 0, "email_inserts": 0}

    def fake_execute(sql, params=None):
        if "INSERT INTO quarantined_emails" in sql:
            state["quarantine_attempts"] += 1
            key = (params["source"], params["folder"], params["uid"])
            state["quarantine_keys"].add(key)
            # Model PostgreSQL INSERT ... ON CONFLICT DO NOTHING.
            return SimpleNamespace(rowcount=1 if len(state["quarantine_keys"]) == 1 else 0)
        if "INSERT INTO emails" in sql:
            state["email_inserts"] += 1
        return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "get_clamav_scanner", lambda: _VirusScanner())
    monkeypatch.setattr(worker, "create_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "log_error", lambda *args, **kwargs: None)

    assert worker.store_email("account", "INBOX", 42, RAW_EMAIL) is True
    assert worker.store_email("account", "INBOX", 42, RAW_EMAIL) is True
    assert state["quarantine_attempts"] == 2
    assert len(state["quarantine_keys"]) == 1
    assert state["email_inserts"] == 0


def test_worker_does_not_archive_when_clamav_scan_does_not_complete(monkeypatch):
    """Enabled ClamAV failures must preserve the fetch cursor for retry."""
    state = {"email_inserts": 0}

    def fake_execute(sql, params=None):
        if "INSERT INTO emails" in sql:
            state["email_inserts"] += 1
        return SimpleNamespace(rowcount=1)

    class UnscannedScanner(_VirusScanner):
        def scan(self, _raw):
            return False, None, None, False

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "get_clamav_scanner", lambda: UnscannedScanner())

    with pytest.raises(RuntimeError, match="ClamAV scan did not complete"):
        worker.store_email("account", "INBOX", 42, RAW_EMAIL)
    assert state["email_inserts"] == 0


def test_worker_persists_completed_clean_scan_metadata(monkeypatch):
    """A clean fetched email records both scan completion and its timestamp."""
    state = {"email_insert": None}
    scan_time = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)

    class CleanScanner(_VirusScanner):
        def scan(self, _raw):
            return False, None, scan_time, True

    def fake_execute(sql, params=None):
        if "INSERT INTO emails" in sql:
            state["email_insert"] = params
        return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "get_clamav_scanner", lambda: CleanScanner())

    assert worker.store_email("account", "INBOX", 42, RAW_EMAIL) is True
    assert state["email_insert"]["virus_scanned"] is True
    assert state["email_insert"]["virus_detected"] is False
    assert state["email_insert"]["scan_timestamp"] == scan_time


def test_worker_preserves_completed_scan_metadata_on_unscanned_upsert():
    worker_source = (ROOT / "worker" / "src" / "worker.py").read_text(encoding="utf-8")
    start = worker_source.index("ON CONFLICT (source, folder, uid) DO UPDATE SET")
    end = worker_source.index("quarantined = EXCLUDED.quarantined", start)
    statement = worker_source[start:end]
    assert "WHEN EXCLUDED.virus_scanned THEN TRUE" in statement
    assert "emails.signature = EXCLUDED.signature" in statement
    assert "ELSE FALSE" in statement
    assert "ELSE NULL" in statement


def test_worker_archives_with_explicit_unscanned_metadata_when_disabled(monkeypatch):
    """An intentional global disable remains allowed and is visible as unscanned."""
    state = {"email_insert": None}

    class DisabledScanner(_VirusScanner):
        def requires_scan(self):
            return False

        def is_enabled(self):
            return False

    def fake_execute(sql, params=None):
        if "INSERT INTO emails" in sql:
            state["email_insert"] = params
        return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "get_clamav_scanner", lambda: DisabledScanner())

    assert worker.store_email("account", "INBOX", 42, RAW_EMAIL) is True
    assert state["email_insert"]["virus_scanned"] is False
    assert state["email_insert"]["virus_detected"] is False
    assert state["email_insert"]["scan_timestamp"] is None


def test_worker_quarantine_database_failure_is_retried(monkeypatch):
    """A real quarantine INSERT failure must propagate to preserve fetch state."""
    def fake_execute(sql, params=None):
        if "INSERT INTO quarantined_emails" in sql:
            raise RuntimeError("database unavailable")
        return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(worker, "execute", fake_execute)
    monkeypatch.setattr(worker, "get_clamav_scanner", lambda: _VirusScanner())
    monkeypatch.setattr(worker, "create_alert", lambda *args, **kwargs: None)
    monkeypatch.setattr(worker, "log_error", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError, match="database unavailable"):
        worker.store_email("account", "INBOX", 42, RAW_EMAIL)


def test_manual_duplicate_quarantine_keeps_archive_row(monkeypatch):
    """A duplicate manual insert must not delete the re-fetched archive row."""
    email_row = {
        "id": 10,
        "source": "account",
        "folder": "INBOX",
        "uid": 42,
        "subject": "duplicate quarantine",
        "sender": "sender@example.com",
        "recipients": "recipient@example.com",
        "date": None,
        "date_parsed": None,
        "message_id": None,
        "raw_email": b"raw",
        "signature": "sig",
        "compressed": False,
        "virus_name": "Eicar-Test",
        "virus_scanned": True,
        "virus_detected": True,
        "scan_timestamp": None,
    }

    class Result:
        def mappings(self):
            return self

        def first(self):
            return email_row

    class Connection:
        def __init__(self):
            self.deleted = False

        def execute(self, statement, params=None):
            sql = str(statement)
            if "INSERT INTO quarantined_emails" in sql:
                return SimpleNamespace(rowcount=0)
            if "DELETE FROM emails" in sql:
                self.deleted = True
            return SimpleNamespace(rowcount=1)

    conn = Connection()

    class Transaction:
        def __enter__(self):
            return conn

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(emails_mod, "query", lambda *args, **kwargs: Result())
    monkeypatch.setattr(emails_mod, "transaction", lambda: Transaction())
    monkeypatch.setattr(emails_mod, "log", lambda *args, **kwargs: None)

    assert emails_mod._quarantine_emails([10], "tester") == 0
    assert conn.deleted is False


def test_schema_enforces_one_non_null_quarantine_identity_and_cleans_legacy_duplicates():
    schema = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")

    assert "UPDATE emails e" in schema
    assert "SET quarantine_id = retained.retained_id" in schema
    assert "MIN(id) AS retained_id" in schema
    assert "DELETE FROM quarantined_emails q" in schema
    assert "q.original_source = duplicate.original_source" in schema
    assert "q.original_folder = duplicate.original_folder" in schema
    assert "q.original_uid = duplicate.original_uid" in schema
    assert "q.id > duplicate.retained_id" in schema
    assert (
        "CREATE UNIQUE INDEX IF NOT EXISTS quarantined_emails_source_folder_uid_idx"
        in schema
    )
    assert "WHERE original_uid IS NOT NULL" in schema


def test_worker_quarantine_insert_is_idempotent():
    worker_source = (ROOT / "worker" / "src" / "worker.py").read_text(encoding="utf-8")
    start = worker_source.index("INSERT INTO quarantined_emails")
    end = worker_source.index("# Insert into emails table", start)
    statement = worker_source[start:end]

    assert "ON CONFLICT DO NOTHING" in statement
    assert "original_source, original_folder, original_uid" in statement


def test_manual_quarantine_does_not_delete_archive_copy_on_duplicate():
    emails = (ROOT / "api" / "src" / "routes" / "emails.py").read_text(encoding="utf-8")
    start = emails.index("def _quarantine_emails")
    end = emails.index("def _delete_emails_from_db", start)
    function = emails[start:end]

    assert "ON CONFLICT DO NOTHING" in function
    assert "insert_result.rowcount == 0" in function
    assert "leave this archive row" in function
