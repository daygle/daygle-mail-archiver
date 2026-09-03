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
from datetime import datetime, timezone
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


def test_api_scanner_rejects_false_clamav_ping(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])

    class FalsePing:
        def ping(self):
            return False

    monkeypatch.setattr(api_scanner_mod.pyclamd, "ClamdNetworkSocket", lambda **kwargs: FalsePing())
    assert scanner._connect() is None
    assert scanner._scanner is None


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


def test_api_scanner_accepts_pyclamd_dict_detection(monkeypatch):
    scanner = _api_scanner(monkeypatch, [])
    monkeypatch.setattr(
        ApiScanner,
        "_connect",
        lambda self: _FakeClamd({"stream": ("FOUND", "Eicar-Test-Signature")}),
    )
    detected, name, ts, scanned = scanner.scan(b"eicar")
    assert detected is True and name == "Eicar-Test-Signature"
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


def test_worker_scanner_requires_scan_when_settings_are_unavailable(monkeypatch):
    def failing_query(sql, params=None):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("clamav_scanner.query", failing_query)
    monkeypatch.setattr("clamav_scanner.execute", lambda *args, **kwargs: None)
    scanner = WorkerScanner()
    assert scanner.is_enabled() is False
    assert scanner.requires_scan() is True


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


def test_worker_scanner_reloads_settings_after_startup(monkeypatch):
    settings = [{"key": "clamav_enabled", "value": "true"}]
    monkeypatch.setattr("clamav_scanner.query", lambda sql, params=None: FakeResult(settings))
    scanner = WorkerScanner()
    settings[:] = [{"key": "clamav_enabled", "value": "false"}]
    scanner._last_settings_load = 0
    scanner.refresh_settings()
    assert scanner.is_enabled() is False


def test_worker_scanner_rejects_unknown_scan_response(monkeypatch):
    monkeypatch.setattr("clamav_scanner.query", lambda sql, params=None: FakeResult([]))
    scanner = WorkerScanner()
    monkeypatch.setattr(WorkerScanner, "_connect", lambda self: _FakeClamd({"stream": "unexpected"}))
    detected, _name, ts, scanned = scanner.scan(b"hello")
    assert detected is False and ts is None and scanned is False


def test_worker_scanner_accepts_pyclamd_dict_detection(monkeypatch):
    monkeypatch.setattr("clamav_scanner.query", lambda sql, params=None: FakeResult([]))
    scanner = WorkerScanner()
    monkeypatch.setattr(
        WorkerScanner,
        "_connect",
        lambda self: _FakeClamd({"stream": ("FOUND", "Eicar-Test-Signature")}),
    )
    detected, name, ts, scanned = scanner.scan(b"eicar")
    assert detected is True and name == "Eicar-Test-Signature"
    assert ts is not None and scanned is True


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


def test_scan_emails_requires_scan_permission(monkeypatch):
    _grant(monkeypatch, ["view_emails"])
    response = emails_mod.scan_emails(_req(), ids=[1])
    assert response.status_code == 403


def test_scan_emails_route_reports_summary(monkeypatch):
    _grant(monkeypatch, ["scan_emails"])
    monkeypatch.setattr(
        emails_mod,
        "_scan_email_ids",
        lambda ids, username: {"scanned": 2, "clean": 1, "infected": 1, "skipped": 0, "errors": []},
    )
    request = _req()
    response = emails_mod.scan_emails(request, ids=[1, 2])
    assert response.status_code == 303
    assert response.headers["location"] == "/emails"
    assert "Scanned 2 email(s)." in request.session["flash"]["message"]
    assert request.session["flash"]["type"] == "warning"


def test_scan_emails_route_reports_disabled_scanner(monkeypatch):
    _grant(monkeypatch, ["scan_emails"])
    monkeypatch.setattr(
        emails_mod,
        "_scan_email_ids",
        lambda ids, username: {"scanned": 0, "clean": 0, "infected": 0, "skipped": 0, "errors": ["ClamAV scanning is disabled in Global Settings."]},
    )
    request = _req()
    response = emails_mod.scan_emails(request, ids=[1])
    assert response.status_code == 303
    assert request.session["flash"]["type"] == "error"
    assert "disabled" in request.session["flash"]["message"]


def test_emails_template_exposes_manual_scan_controls():
    template = (API_DIR / "templates" / "emails.html").read_text(encoding="utf-8")
    detail_template = (API_DIR / "templates" / "email-view.html").read_text(encoding="utf-8")
    assert "form.action = '/emails/scan'" in template
    assert "Scan Selected" in template
    assert "Scan with ClamAV" in detail_template
    assert "infected messages for review" in detail_template


def test_manual_scan_persists_clean_result(monkeypatch):
    scan_time = datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc)
    executed = []

    class Scanner:
        def requires_scan(self):
            return True

        def scan(self, raw):
            assert raw == b"raw email"
            return False, None, scan_time, True

    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: Scanner())
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{
            "id": 7, "raw_email": gzip.compress(b"raw email"),
            "compressed": True, "signature": None, "quarantined": False,
        }]),
    )
    monkeypatch.setattr(
        emails_mod,
        "execute",
        lambda sql, params=None: executed.append((sql, params)) or SimpleNamespace(rowcount=1),
    )

    result = emails_mod._scan_email_ids([7], "tester")
    assert result == {"scanned": 1, "clean": 1, "infected": 0, "skipped": 0, "errors": []}
    params = executed[0][1]
    assert params["virus_scanned"] is True
    assert params["virus_detected"] is False
    assert params["virus_name"] is None
    assert params["scan_timestamp"] == scan_time


def test_manual_scan_persists_infected_result_and_alerts(monkeypatch):
    scan_time = datetime(2026, 8, 6, 16, 1, tzinfo=timezone.utc)
    executed = []
    alerts = []

    class Scanner:
        def requires_scan(self):
            return True

        def scan(self, raw):
            return True, "Eicar-Test-Signature", scan_time, True

    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: Scanner())
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{
            "id": 8, "raw_email": gzip.compress(b"infected email"),
            "compressed": True, "signature": None, "quarantined": False,
        }]),
    )
    monkeypatch.setattr(
        emails_mod,
        "execute",
        lambda sql, params=None: executed.append((sql, params)) or SimpleNamespace(rowcount=1),
    )
    monkeypatch.setattr(emails_mod, "create_alert", lambda *args, **kwargs: alerts.append((args, kwargs)))

    result = emails_mod._scan_email_ids([8], "tester")
    assert result["infected"] == 1
    assert result["clean"] == 0
    assert executed[0][1]["virus_detected"] is True
    assert executed[0][1]["virus_name"] == "Eicar-Test-Signature"
    assert alerts and alerts[0][1]["trigger_key"] == "virus_detected"


def test_manual_scan_does_not_overwrite_changed_email(monkeypatch):
    scan_time = datetime(2026, 8, 6, 16, 2, tzinfo=timezone.utc)
    executed = []

    class Scanner:
        def requires_scan(self):
            return True

        def scan(self, raw):
            return False, None, scan_time, True

    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: Scanner())
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{
            "id": 10, "raw_email": b"original", "compressed": False,
            "signature": "old-signature", "quarantined": False,
        }]),
    )

    def fake_execute(sql, params=None):
        executed.append((sql, params))
        return SimpleNamespace(rowcount=0)

    monkeypatch.setattr(emails_mod, "execute", fake_execute)
    result = emails_mod._scan_email_ids([10], "tester")
    assert result["scanned"] == 0
    assert result["skipped"] == 1
    assert "changed" in result["errors"][0]
    assert "raw_email IS NOT DISTINCT FROM :original_raw" in executed[0][0]
    assert "compressed IS NOT DISTINCT FROM :original_compressed" in executed[0][0]


def test_manual_scan_reports_database_update_failure(monkeypatch):
    class Scanner:
        def requires_scan(self):
            return True

        def scan(self, raw):
            return False, None, datetime.now(timezone.utc), True

    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: Scanner())
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{
            "id": 11, "raw_email": b"raw email", "compressed": False,
            "signature": None, "quarantined": False,
        }]),
    )
    monkeypatch.setattr(emails_mod, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))

    result = emails_mod._scan_email_ids([11], "tester")
    assert result["scanned"] == 0
    assert result["skipped"] == 1
    assert "could not be saved" in result["errors"][0]


def test_manual_scan_reports_unavailable_without_update(monkeypatch):
    executed = []

    class Scanner:
        def requires_scan(self):
            return True

        def scan(self, raw):
            return False, None, None, False

    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: Scanner())
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{
            "id": 9, "raw_email": b"raw email", "compressed": False, "signature": None, "quarantined": False,
        }]),
    )
    monkeypatch.setattr(
        emails_mod,
        "execute",
        lambda sql, params=None: executed.append((sql, params)) or SimpleNamespace(rowcount=1),
    )

    result = emails_mod._scan_email_ids([9], "tester")
    assert result["scanned"] == 0
    assert result["skipped"] == 1
    assert result["errors"]
    assert executed == []


# ---------------------------------------------------------------------------
# list_emails integrity: signature-less rows short-circuit to "No Sig"
# ---------------------------------------------------------------------------

def _list_emails_fake_query(rows):
    def handler(sql, params=None):
        if "COUNT(*) AS c" in sql or "COUNT(*) as c" in sql:
            return FakeResult([{"c": len(rows)}])
        if "AS total_emails" in sql:
            # Archive-wide stat cards query (see list_emails); page rows double as the archive.
            return FakeResult([{"total_emails": len(rows), "infected_emails": 0, "unscanned_emails": 0, "quarantined_emails": 0}])
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
            "virus_detected": False, "virus_name": None, "scan_timestamp": None,
            "signature": None, "raw_email": None, "compressed": None,
        },
        {  # signature present -> integrity ok
            "id": 2, "source": "acct", "folder": "INBOX", "uid": 2,
            "subject": "current", "sender": "a@b.c", "recipients": "x@y.z",
            "date": None, "created_at": None, "virus_scanned": True,
            "virus_detected": False, "virus_name": None,
            "scan_timestamp": datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc),
            "signature": sig, "raw_email": gzip.compress(b"raw bytes"), "compressed": True,
        },
    ]
    monkeypatch.setattr(emails_mod, "query", _list_emails_fake_query(rows))
    monkeypatch.setattr(
        emails_mod,
        "format_datetime",
        lambda value, _user_id: value.strftime("%Y-%m-%d %H:%M"),
    )

    response = emails_mod.list_emails(_req(), page=1)
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)

    assert "No Sig" in html       # legacy row: no signature
    assert "Valid" in html        # current row: hash matches
    assert "Scanned - clean" in html
    assert "2026-08-06 15:30" in html
    assert "Invalid" not in html


def test_emails_template_guards_scroll_button_lookup():
    template = (API_DIR / "templates" / "emails.html").read_text(encoding="utf-8")
    assert "if (!btn) return;" in template
    assert "ClamAV scan completed: no virus detected" in template
    assert "scanning is disabled" in template


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
