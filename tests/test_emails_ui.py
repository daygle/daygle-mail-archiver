"""
Unit tests for the emails UI audit fixes.

Covers:
- Missing permission checks on view_email / perform_delete / perform_quarantine.
- ClamAV scan honesty: ``scan()`` reports whether a scan actually ran, so an
  unavailable or disabled daemon can never mark an email as ``virus_scanned``.
- The list page short-circuits integrity for signature-less rows and the detail
  page renders legacy NULL/corrupt raw emails gracefully instead of 500ing.
- IMAP folder quoting for mail-server deletion.

Run with:  python -m pytest tests/ -v
"""

import gzip
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
WORKER_SRC = Path(__file__).resolve().parent.parent / "worker" / "src"
sys.path.insert(0, str(WORKER_SRC))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import emails as emails_mod  # noqa: E402
from src.utils import clamav_scanner as api_scanner_mod  # noqa: E402
from src.utils.clamav_scanner import ClamAVScanner as ApiScanner  # noqa: E402
from src.utils.email_parser import compute_signature  # noqa: E402
from src.utils.permissions import PermissionChecker  # noqa: E402
from clamav_scanner import ClamAVScanner as WorkerScanner  # noqa: E402


class FakeResult:
    """Mimics the MaterializedResult surface used by route code."""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeURL:
    path = "/emails"


def _req(user_id=1, perms=None):
    session = {"user_id": user_id, "username": "tester"}
    if perms is not None:
        session["permissions"] = perms
    # TemplatesWrapper reads request.scope["session"] and base.html reads
    # request.url.path for nav highlighting
    return SimpleNamespace(session=session, scope={"session": session}, url=_FakeURL())


def _grant(monkeypatch, perms):
    monkeypatch.setattr(PermissionChecker, "_load_user_permissions", lambda self: list(perms))


# ---------------------------------------------------------------------------
# IMAP folder quoting (mail-server deletion path)
# ---------------------------------------------------------------------------

def test_quote_imap_folder():
    assert emails_mod._quote_imap_folder("INBOX") == "INBOX"
    assert emails_mod._quote_imap_folder("Sent Items") == '"Sent Items"'
    assert emails_mod._quote_imap_folder('Say "Hi"') == r'"Say \"Hi\""'
    assert emails_mod._quote_imap_folder("Tab\tHere") == '"Tab\tHere"'


# ---------------------------------------------------------------------------
# API scanner: scan() honesty
# ---------------------------------------------------------------------------

def _api_scanner(monkeypatch, settings_rows):
    monkeypatch.setattr(
        api_scanner_mod,
        "query",
        lambda sql, params=None: FakeResult(settings_rows),
    )
    # log() writes to the logs table; keep it a no-op in unit tests
    monkeypatch.setattr(api_scanner_mod, "log", lambda *a, **k: None)
    return ApiScanner()


class _FakeClamd:
    def __init__(self, result=None):
        self._result = result

    def scan_stream(self, data):
        return self._result


def test_api_scanner_scanned_false_when_unavailable(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])
    monkeypatch.setattr(ApiScanner, "_connect", lambda self: None)
    detected, name, ts, scanned = scanner.scan(b"hello")
    assert detected is False
    assert name is None
    assert ts is None
    assert scanned is False  # must NOT be marked as virus_scanned


def test_api_scanner_scanned_false_when_disabled(monkeypatch):
    scanner = _api_scanner(monkeypatch, [{"key": "clamav_enabled", "value": "false"}])
    detected, _name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and ts is None and scanned is False


def test_api_scanner_scanned_false_when_oversized(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])
    scanner.MAX_SCAN_SIZE = 5
    detected, _name, ts, scanned = scanner.scan(b"x" * 10)
    assert detected is False and ts is None and scanned is False


def test_api_scanner_scanned_true_on_clean_scan(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])
    monkeypatch.setattr(ApiScanner, "_connect", lambda self: _FakeClamd(None))
    detected, name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and name is None
    assert ts is not None and scanned is True


def test_api_scanner_scanned_true_on_virus_found(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])
    monkeypatch.setattr(ApiScanner, "_connect", lambda self: _FakeClamd(("FOUND", "Eicar-Test")))
    detected, name, ts, scanned = scanner.scan(b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR")
    assert detected is True
    assert name == "Eicar-Test"
    assert ts is not None and scanned is True


# ---------------------------------------------------------------------------
# Worker scanner: same 4-tuple semantics
# ---------------------------------------------------------------------------

def test_worker_scanner_scanned_false_when_disabled(monkeypatch):
    monkeypatch.setattr(
        "clamav_scanner.query",
        lambda sql, params=None: FakeResult([{"key": "clamav_enabled", "value": "false"}]),
    )
    scanner = WorkerScanner()
    detected, _name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and ts is None and scanned is False


def test_worker_scanner_scanned_false_when_unavailable(monkeypatch):
    monkeypatch.setattr(
        "clamav_scanner.query",
        lambda sql, params=None: FakeResult([]),
    )
    scanner = WorkerScanner()
    monkeypatch.setattr(WorkerScanner, "_connect", lambda self: None)
    detected, _name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and ts is None and scanned is False


def test_worker_scanner_scanned_true_on_real_scan(monkeypatch):
    monkeypatch.setattr("clamav_scanner.query", lambda sql, params=None: FakeResult([]))
    scanner = WorkerScanner()
    monkeypatch.setattr(WorkerScanner, "_connect", lambda self: _FakeClamd(None))
    detected, _name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and ts is not None and scanned is True


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------

def test_view_email_requires_view_emails(monkeypatch):
    _grant(monkeypatch, ["view_reports"])
    response = emails_mod.view_email(_req(), email_id=1)
    assert response.status_code == 403


def test_perform_delete_requires_delete_emails(monkeypatch):
    _grant(monkeypatch, ["view_emails", "manage_quarantine"])
    response = emails_mod.perform_delete(_req(), ids=[1], mode="db")
    assert response.status_code == 403


def test_perform_quarantine_requires_manage_quarantine(monkeypatch):
    _grant(monkeypatch, ["view_emails", "delete_emails"])
    response = emails_mod.perform_quarantine(_req(), ids=[1])
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# list_emails integrity: signature-less rows short-circuit to "No Sig"
# ---------------------------------------------------------------------------

def _list_emails_fake_query(rows):
    def handler(sql, params=None):
        if "COUNT(*) AS c" in sql or "COUNT(*) as c" in sql:
            return FakeResult([{"c": len(rows)}])
        if "FROM emails" in sql and "LIMIT" in sql:
            return FakeResult(rows)
        if "FROM settings" in sql:
            return FakeResult([
                {"key": "date_format", "value": "%Y-%m-%d"},
                {"key": "time_format", "value": "%H:%M"},
            ])
        if "FROM users" in sql:
            return FakeResult([])
        raise AssertionError(f"Unexpected query: {sql[:120]}")
    return handler


def test_list_emails_no_signature_rows_short_circuit(monkeypatch):
    _grant(monkeypatch, ["view_emails"])
    sig = compute_signature(b"raw bytes")
    rows = [
        {  # signature NULL -> no_signature, and the SQL CASE leaves raw NULL
            "id": 1, "source": "acct", "folder": "INBOX", "uid": 1,
            "subject": "legacy", "sender": "a@b.c", "recipients": "x@y.z",
            "date": None, "created_at": None, "virus_scanned": True,
            "virus_detected": False, "virus_name": None,
            "signature": None, "raw_email": None, "compressed": None,
        },
        {  # signature present -> integrity ok
            "id": 2, "source": "acct", "folder": "INBOX", "uid": 2,
            "subject": "current", "sender": "a@b.c", "recipients": "x@y.z",
            "date": None, "created_at": None, "virus_scanned": True,
            "virus_detected": False, "virus_name": None,
            "signature": sig, "raw_email": gzip.compress(b"raw bytes"), "compressed": True,
        },
    ]
    monkeypatch.setattr(emails_mod, "query", _list_emails_fake_query(rows))

    response = emails_mod.list_emails(_req(), page=1)
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)

    assert "No Sig" in html       # legacy row: no signature
    assert "Valid" in html        # current row: hash matches
    assert "Invalid" not in html


# ---------------------------------------------------------------------------
# view_email: NULL raw email renders gracefully (no 500)
# ---------------------------------------------------------------------------

def test_view_email_handles_null_raw(monkeypatch):
    _grant(monkeypatch, ["view_emails"])
    monkeypatch.setattr(emails_mod, "log", lambda *a, **k: None)

    email_row = {
        "id": 5, "source": "acct", "folder": "INBOX", "uid": 1,
        "subject": "legacy null raw", "sender": "a@b.c", "recipients": "x@y.z",
        "date": None, "message_id": None, "raw_email": None, "compressed": False,
        "signature": None, "created_at": None, "virus_scanned": False,
        "virus_detected": False, "virus_name": None, "scan_timestamp": None,
        "quarantined": False,
    }

    def handler(sql, params=None):
        if "FROM emails" in sql and "WHERE id" in sql:
            return FakeResult([email_row])
        if "FROM settings" in sql:
            return FakeResult([
                {"key": "date_format", "value": "%Y-%m-%d"},
                {"key": "time_format", "value": "%H:%M"},
            ])
        if "FROM users" in sql:
            return FakeResult([])
        raise AssertionError(f"Unexpected query: {sql[:120]}")

    monkeypatch.setattr(emails_mod, "query", handler)

    response = emails_mod.view_email(_req(), email_id=5)
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    assert "No raw email data available" in html
    assert "No preview available" in html
