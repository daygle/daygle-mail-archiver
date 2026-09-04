"""
Unit tests for the Import/Export Emails audit fixes.

Covers:
- The mbox import path now uses the real ``mailbox.mbox`` parser (via a temp
  file) instead of the naive ``split(b"\\nFrom ")`` fallback, which produced
  a garbage message from mbox preambles and left mbox envelope lines inside
  stored messages.
- ``_insert_raw_email`` reports a tri-state status so virus rejections are not
  reported as "failed to insert".
- The import result flash combines success + issues into one message (the
  session flash is a single slot), caps the error list, and uses an info
  flash when nothing was imported.
- The transfer page is reachable with *either* import or export permission,
  with each card gated individually.

Run with:  python -m pytest tests/test_import_export.py -v
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import emails as emails_mod  # noqa: E402
from src.utils.permissions import PermissionChecker  # noqa: E402


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
    path = "/emails/import-export"


def _req(user_id=1):
    session = {"user_id": user_id, "username": "tester"}
    return SimpleNamespace(session=session, scope={"session": session}, url=_FakeURL())


def _grant(monkeypatch, perms):
    monkeypatch.setattr(PermissionChecker, "_load_user_permissions", lambda self: list(perms))


# ---------------------------------------------------------------------------
# _iter_mbox_messages
# ---------------------------------------------------------------------------

# A real-world style mbox: preamble junk before the first boundary, two
# messages, and an mbox-escaped ">From " line inside the first body.
MBOX = (
    b"Preamble junk that is not part of any message\n\n"
    b"From sender@example.com Tue Jan  2 12:00:00 2024\n"
    b"Subject: First\nFrom: sender@example.com\nTo: recv@example.com\n\n"
    b"Body line one.\n>From this line was escaped.\n\n"
    b"From sender@example.com Tue Jan  2 13:00:00 2024\n"
    b"Subject: Second\nFrom: sender@example.com\nTo: recv@example.com\n\n"
    b"Second body.\n"
)


def test_iter_mbox_messages_parses_real_mbox():
    messages = list(emails_mod._iter_mbox_messages(MBOX))
    assert len(messages) == 2
    # Preamble must not become a message, and messages must not include the
    # mbox envelope "From " line.
    assert all(b"Preamble junk" not in m for m in messages)
    assert messages[0].startswith(b"Subject: First")
    assert b"Body line one." in messages[0]
    assert b">From this line was escaped." in messages[0]
    assert messages[1].startswith(b"Subject: Second")
    assert b"Second body." in messages[1]


def test_iter_mbox_messages_handles_empty_file():
    assert list(emails_mod._iter_mbox_messages(b"")) == []


def test_iter_mbox_messages_cleans_up_temp_file(monkeypatch):
    unlinked = []
    real_unlink = os.unlink

    def fake_unlink(path):
        unlinked.append(path)
        real_unlink(path)

    monkeypatch.setattr(os, "unlink", fake_unlink)
    assert len(list(emails_mod._iter_mbox_messages(MBOX))) == 2
    assert len(unlinked) == 1  # the temp file was removed after parsing


def test_iter_mbox_messages_falls_back_for_file_without_envelope():
    # A bare .eml renamed to .mbox has no "From " envelope line; the mailbox
    # parser yields nothing, so it must be treated as a single message rather
    # than silently importing nothing (old manual-parser behaviour).
    raw = b"Subject: Lone\r\nFrom: a@b.c\r\n\r\nBody\r\n"
    messages = list(emails_mod._iter_mbox_messages(raw))
    assert messages == [raw]


# ---------------------------------------------------------------------------
# _build_import_flash
# ---------------------------------------------------------------------------

def test_build_import_flash_nothing_imported():
    msg, category = emails_mod._build_import_flash(0, 0, [])
    assert msg == "No messages were imported."
    assert category == "info"


def test_build_import_flash_success():
    msg, category = emails_mod._build_import_flash(3, 0, [])
    assert msg == "Imported 3 message(s)."
    assert category == "success"


def test_build_import_flash_rejected_only_is_warning():
    msg, category = emails_mod._build_import_flash(0, 2, [])
    assert "2 message(s) blocked (virus detected)." in msg
    assert category == "warning"


def test_build_import_flash_combines_success_and_errors():
    msg, category = emails_mod._build_import_flash(2, 0, ["a.eml: failed to insert"])
    assert "Imported 2 message(s)." in msg
    assert "a.eml: failed to insert" in msg
    assert category == "error"


def test_build_import_flash_caps_error_list():
    errors = [f"file{i}: failed to insert" for i in range(25)]
    msg, category = emails_mod._build_import_flash(0, 0, errors)
    assert category == "error"
    assert msg.startswith("No messages were imported.")
    assert "file0" in msg and "file19" in msg
    assert "file24" not in msg
    assert "... and 5 more issue(s)" in msg


def test_build_import_flash_mixed_outcome_is_warning():
    msg, category = emails_mod._build_import_flash(3, 2, [])
    assert "Imported 3 message(s)." in msg
    assert "2 message(s) blocked (virus detected)." in msg
    assert category == "warning"


# ---------------------------------------------------------------------------
# _record_insert_result tri-state dispatch helper
# ---------------------------------------------------------------------------

def test_record_insert_result_mapping():
    assert emails_mod._record_insert_result("ok", "a.eml") == (1, 0, None)
    assert emails_mod._record_insert_result("rejected", "a.eml") == (0, 1, None)
    imp, rej, err = emails_mod._record_insert_result("error", "a.eml")
    assert (imp, rej) == (0, 0)
    assert err == "a.eml: failed to insert"


# ---------------------------------------------------------------------------
# _insert_raw_email tri-state status
# ---------------------------------------------------------------------------

class _FakeScanner:
    def __init__(self, enabled=True, result=(False, None, None, True)):
        self._enabled = enabled
        self._result = result

    def requires_scan(self):
        return self._enabled

    def is_enabled(self):
        return self._enabled

    def scan(self, raw):
        return self._result


RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: recv@example.com\r\n"
    b"Subject: Import test\r\n"
    b"Date: Tue, 02 Jan 2024 12:00:00 +0000\r\n"
    b"\r\n"
    b"Hello world\r\n"
)


def _fake_db(monkeypatch, execute_raises=None):
    monkeypatch.setattr(emails_mod, "log", lambda *a, **k: None)
    monkeypatch.setattr(emails_mod, "create_alert", lambda *a, **k: None)
    monkeypatch.setattr(
        emails_mod,
        "query",
        lambda sql, params=None: FakeResult([{"next_uid": -1}]),
    )

    def fake_execute(sql, params=None):
        if execute_raises:
            raise execute_raises
        return None

    monkeypatch.setattr(emails_mod, "execute", fake_execute)


def test_insert_raw_email_ok_when_scanning_disabled(monkeypatch):
    _fake_db(monkeypatch)
    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: _FakeScanner(enabled=False))
    assert emails_mod._insert_raw_email(RAW_EMAIL, _req()) == "ok"


def test_insert_raw_email_ok_on_clean_scan(monkeypatch):
    _fake_db(monkeypatch)
    scanner = _FakeScanner(enabled=True, result=(False, None, datetime.now(timezone.utc), True))
    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: scanner)
    assert emails_mod._insert_raw_email(RAW_EMAIL, _req()) == "ok"


def test_insert_raw_email_errors_when_enabled_scan_unavailable(monkeypatch):
    _fake_db(monkeypatch)
    scanner = _FakeScanner(enabled=True, result=(False, None, None, False))
    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: scanner)
    assert emails_mod._insert_raw_email(RAW_EMAIL, _req()) == "error"


def test_insert_raw_email_rejected_on_virus(monkeypatch):
    _fake_db(monkeypatch)
    alerts = []
    monkeypatch.setattr(emails_mod, "create_alert", lambda *a, **k: alerts.append(a))
    scanner = _FakeScanner(enabled=True, result=(True, "Eicar-Test", datetime.now(timezone.utc), True))
    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: scanner)
    assert emails_mod._insert_raw_email(RAW_EMAIL, _req()) == "rejected"
    assert len(alerts) == 1  # security alert raised for the infection


def test_insert_raw_email_error_on_insert_failure(monkeypatch):
    _fake_db(monkeypatch, execute_raises=RuntimeError("db down"))
    scanner = _FakeScanner(enabled=True, result=(False, None, datetime.now(timezone.utc), True))
    monkeypatch.setattr(emails_mod, "_get_import_scanner", lambda: scanner)
    assert emails_mod._insert_raw_email(RAW_EMAIL, _req()) == "error"


# ---------------------------------------------------------------------------
# Transfer page permission gating
# ---------------------------------------------------------------------------

def _transfer_accounts_query(sql, params=None):
    if "FROM fetch_accounts" in sql:
        return FakeResult([{"name": "acct1"}, {"name": "acct2"}])
    raise AssertionError(f"Unexpected query: {sql[:120]}")


def test_transfer_page_denied_without_permissions(monkeypatch):
    _grant(monkeypatch, ["view_emails"])
    response = emails_mod.emails_transfer_page(_req())
    assert response.status_code == 403


def test_transfer_page_allowed_with_export_only(monkeypatch):
    _grant(monkeypatch, ["export_emails"])
    monkeypatch.setattr(emails_mod, "query", _transfer_accounts_query)
    response = emails_mod.emails_transfer_page(_req())
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    # Export card body text is unique to the export card; the import card's
    # submit button must not be rendered without import_emails.
    assert "Export all emails for a selected fetch account" in html
    assert '<button type="submit" form="import-form"' not in html


def test_transfer_page_allowed_with_import_only(monkeypatch):
    _grant(monkeypatch, ["import_emails"])
    monkeypatch.setattr(emails_mod, "query", _transfer_accounts_query)
    response = emails_mod.emails_transfer_page(_req())
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    assert '<button type="submit" form="import-form"' in html
    assert "<span>Upload/Import</span>" in html
    assert "Export all emails for a selected fetch account" not in html


def test_transfer_page_shows_both_cards_with_both_permissions(monkeypatch):
    _grant(monkeypatch, ["import_emails", "export_emails"])
    monkeypatch.setattr(emails_mod, "query", _transfer_accounts_query)
    response = emails_mod.emails_transfer_page(_req())
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    assert '<button type="submit" form="import-form"' in html
    assert "<span>Upload/Import</span>" in html
    assert "Export all emails for a selected fetch account" in html
    assert "acct1" in html and "acct2" in html


def test_transfer_page_empty_accounts_guidance(monkeypatch):
    _grant(monkeypatch, ["import_emails", "export_emails"])
    monkeypatch.setattr(emails_mod, "query", lambda sql, params=None: FakeResult([]))
    response = emails_mod.emails_transfer_page(_req())
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    assert "No fetch accounts exist yet" in html
