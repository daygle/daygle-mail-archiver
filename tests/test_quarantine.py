"""
Unit tests for the quarantine pages / restore flow audit.

Covers:
- Restore preserves the ORIGINAL signature and all ClamAV scan metadata
  (virus_scanned / virus_detected / virus_name / scan_timestamp) plus
  date_parsed / message_id, so integrity checks stay meaningful after restore.
- Encrypted quarantine blobs are decrypted on restore; undecryptable blobs
  fail cleanly with the quarantine record left in place.
- Restore is conflict-safe: an existing email with the same (source, folder,
  uid) is never silently overwritten (which could replace a clean re-fetched
  copy with infected content).
- Legacy rows with a wrong compressed flag are uncompressed on restore so the
  restored email still passes its integrity check.
- The quarantine list short-circuits signature-less rows (no raw fetch /
  decrypt / decompress) and the mail-server delete path quotes IMAP folders
  and treats negative (imported) UIDs as database-only.

Run with:  python -m pytest tests/test_quarantine.py -v
"""

import gzip
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

from src.routes import quarantine as quarantine_mod  # noqa: E402
from src.utils.email_parser import compute_signature  # noqa: E402
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
    path = "/quarantine"


def _req(user_id=1):
    session = {"user_id": user_id, "username": "tester"}
    return SimpleNamespace(session=session, scope={"session": session}, url=_FakeURL())


def _grant(monkeypatch, perms):
    monkeypatch.setattr(PermissionChecker, "_load_user_permissions", lambda self: list(perms))


RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: recv@example.com\r\n"
    b"Subject: Quarantine test\r\n"
    b"Date: Tue, 02 Jan 2024 12:00:00 +0000\r\n"
    b"Message-ID: <abc123@example.com>\r\n"
    b"\r\n"
    b"Hello world\r\n"
)

SIG = compute_signature(RAW_EMAIL)
SCAN_TS = datetime(2024, 1, 2, 12, 0, 0, tzinfo=timezone.utc)
DATE_PARSED = datetime(2024, 1, 2, 11, 30, 0, tzinfo=timezone.utc)


def _item(**overrides):
    item = {
        "id": 7,
        "original_source": "acct",
        "original_folder": "INBOX",
        "original_uid": 42,
        "subject": "Quarantine test",
        "sender": "sender@example.com",
        "recipients": "recv@example.com",
        "date": "Tue, 02 Jan 2024 12:00:00 +0000",
        "date_parsed": DATE_PARSED,
        "message_id": "<abc123@example.com>",
        "raw_email": gzip.compress(RAW_EMAIL),
        "signature": SIG,
        "compressed": True,
        "virus_name": "Eicar-Test",
        "virus_scanned": True,
        "virus_detected": True,
        "scan_timestamp": SCAN_TS,
        "reason": "quarantined by ClamAV",
        "quarantined_at": DATE_PARSED,
        "expires_at": None,
        "quarantined_by": "clamav",
    }
    item.update(overrides)
    return item


# ---------------------------------------------------------------------------
# _prepare_restore_item: metadata preservation through restore
# ---------------------------------------------------------------------------

def test_prepare_restore_item_preserves_signature_and_scan_metadata():
    payload, error = quarantine_mod._prepare_restore_item(_item(), None)
    assert error is None
    # Original signature must be preserved, never recomputed
    assert payload["signature"] == SIG
    # Raw bytes round-trip exactly
    assert gzip.decompress(payload["raw"]) == RAW_EMAIL
    # Scan metadata and identity survive unchanged
    assert payload["vscanned"] is True
    assert payload["vdetected"] is True
    assert payload["vname"] == "Eicar-Test"
    assert payload["scan_ts"] == SCAN_TS
    assert payload["date_parsed"] == DATE_PARSED
    assert payload["message_id"] == "<abc123@example.com>"
    assert payload["source"] == "acct"
    assert payload["folder"] == "INBOX"
    assert payload["uid"] == 42


def test_prepare_restore_item_decrypts_encrypted_blob():
    key = Fernet.generate_key()
    fernet = Fernet(key)
    item = _item(raw_email=fernet.encrypt(gzip.compress(RAW_EMAIL)))
    payload, error = quarantine_mod._prepare_restore_item(item, fernet)
    assert error is None
    assert gzip.decompress(payload["raw"]) == RAW_EMAIL


def test_prepare_restore_item_fails_on_undecryptable_blob():
    fernet_a = Fernet(Fernet.generate_key())
    fernet_b = Fernet(Fernet.generate_key())
    item = _item(raw_email=fernet_a.encrypt(gzip.compress(RAW_EMAIL)))
    payload, error = quarantine_mod._prepare_restore_item(item, fernet_b)
    assert payload is None
    assert "could not be decrypted" in error


def test_prepare_restore_item_legacy_scan_defaults_from_virus_name():
    # Pre-migration rows have NULL scan metadata; virus_name implies the scan
    # happened and detected something.
    payload, error = quarantine_mod._prepare_restore_item(
        _item(virus_scanned=None, virus_detected=None), None
    )
    assert error is None
    assert payload["vscanned"] is True
    assert payload["vdetected"] is True


def test_prepare_restore_item_legacy_manual_quarantine_defaults():
    payload, error = quarantine_mod._prepare_restore_item(
        _item(virus_scanned=None, virus_detected=None, virus_name=None), None
    )
    assert error is None
    assert payload["vscanned"] is True
    assert payload["vdetected"] is False


def test_prepare_restore_item_handles_legacy_wrong_compressed_flag():
    # Blob is actually gzip but the flag says False (legacy data): restore must
    # still uncompress it so the restored email keeps a valid integrity check.
    payload, error = quarantine_mod._prepare_restore_item(
        _item(compressed=False), None
    )
    assert error is None
    assert gzip.decompress(payload["raw"]) == RAW_EMAIL


def test_prepare_restore_item_rejects_missing_raw():
    payload, error = quarantine_mod._prepare_restore_item(_item(raw_email=None), None)
    assert payload is None
    assert "no raw data" in error


def test_prepare_restore_item_rejects_missing_uid():
    payload, error = quarantine_mod._prepare_restore_item(_item(original_uid=None), None)
    assert payload is None
    assert "no original UID" in error


# ---------------------------------------------------------------------------
# _restore_quarantine_item: conflict-safe restore
# ---------------------------------------------------------------------------

class _FakeCursorResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeConn:
    def __init__(self, insert_rowcount):
        self.executed = []
        self._insert_rowcount = insert_rowcount

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))
        if "INSERT INTO emails" in str(sql):
            return _FakeCursorResult(self._insert_rowcount)
        return _FakeCursorResult(1)


class _FakeTxn:
    def __init__(self, insert_rowcount):
        self.conn = _FakeConn(insert_rowcount)

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False  # propagate exceptions like the real transaction()


def _restore_with(monkeypatch, insert_rowcount, item=None):
    monkeypatch.setattr(quarantine_mod, "_get_quarantine_fernet", lambda: None)
    monkeypatch.setattr(
        quarantine_mod,
        "_prepare_restore_item",
        lambda it, f: ({
            "source": "acct", "folder": "INBOX", "uid": 42,
            "subject": "s", "sender": "a@b.c", "recipients": "x@y.z",
            "date": None, "date_parsed": None, "message_id": None,
            "raw": gzip.compress(RAW_EMAIL), "signature": SIG,
            "vscanned": True, "vdetected": True, "vname": "Eicar-Test",
            "scan_ts": None,
        }, None),
    )
    txn = _FakeTxn(insert_rowcount)
    monkeypatch.setattr(quarantine_mod, "transaction", lambda: txn)
    ok, err = quarantine_mod._restore_quarantine_item(item or _item())
    return ok, err, txn.conn


def test_restore_conflict_keeps_quarantine_record(monkeypatch):
    ok, err, conn = _restore_with(monkeypatch, insert_rowcount=0)
    assert ok is False
    assert "already exists" in err
    # The quarantine DELETE must NOT run when the restore conflicts
    assert not any("DELETE FROM quarantined_emails" in sql for sql, _ in conn.executed)


def test_restore_success_deletes_quarantine_record(monkeypatch):
    ok, err, conn = _restore_with(monkeypatch, insert_rowcount=1)
    assert ok is True
    assert err is None
    assert any("INSERT INTO emails" in sql for sql, _ in conn.executed)
    assert any("DELETE FROM quarantined_emails" in sql for sql, _ in conn.executed)


def test_restore_failure_keeps_quarantine_record(monkeypatch):
    monkeypatch.setattr(quarantine_mod, "_get_quarantine_fernet", lambda: None)
    monkeypatch.setattr(
        quarantine_mod,
        "_prepare_restore_item",
        lambda it, f: (None, "cannot prepare"),
    )
    ok, err = quarantine_mod._restore_quarantine_item(_item())
    assert ok is False
    assert "cannot prepare" in err


# ---------------------------------------------------------------------------
# Fernet token helpers
# ---------------------------------------------------------------------------

def test_looks_like_fernet_token():
    key = Fernet.generate_key()
    token = Fernet(key).encrypt(b"payload")
    assert quarantine_mod._looks_like_fernet_token(token) is True
    assert quarantine_mod._looks_like_fernet_token(b"plain bytes") is False
    assert quarantine_mod._looks_like_fernet_token(None) is False
    assert quarantine_mod._looks_like_fernet_token(memoryview(token)) is True


def test_decrypt_quarantine_raw_plain_and_decrypted():
    assert quarantine_mod._decrypt_quarantine_raw(b"plain", None) == (b"plain", "plain")
    key = Fernet.generate_key()
    fernet = Fernet(key)
    enc = fernet.encrypt(b"secret")
    data, status = quarantine_mod._decrypt_quarantine_raw(memoryview(enc), fernet)
    assert status == "decrypted"
    assert data == b"secret"


# ---------------------------------------------------------------------------
# list_quarantine: signature-less rows short-circuit
# ---------------------------------------------------------------------------

def _list_fake_query(rows, captured):
    def handler(sql, params=None):
        captured.append(sql)
        if "FROM users" in sql:
            return FakeResult([])
        if "COUNT(*)" in sql:
            return FakeResult([{"total": len(rows)}])
        if "FROM quarantined_emails" in sql and "LIMIT" in sql:
            return FakeResult(rows)
        raise AssertionError(f"Unexpected query: {str(sql)[:120]}")
    return handler


def _boom(name):
    def fn(*a, **k):
        raise AssertionError(f"{name} should not be called")
    return fn


def test_quarantine_template_binds_scroll_button_after_rendering():
    template = (API_DIR / "templates" / "quarantine.html").read_text(encoding="utf-8")
    button_pos = template.index('id="scroll-to-top-btn"')
    script_pos = template.index("scrollBtn.addEventListener")
    assert button_pos < script_pos
    assert "if (!scrollBtn) return;" in template


def test_list_quarantine_short_circuits_no_signature_rows(monkeypatch):
    captured = []
    rows = [{
        "id": 1, "original_source": "acct", "original_folder": "INBOX",
        "date": None, "subject": "legacy", "sender": "a@b.c",
        "recipients": "x@y.z", "virus_name": None,
        "quarantined_at": None, "expires_at": None, "signature": None,
        "raw_email": None, "compressed": None,
    }]
    monkeypatch.setattr(quarantine_mod, "query", _list_fake_query(rows, captured))
    # decrypt/decompress/hash must never run for signature-less rows
    monkeypatch.setattr(quarantine_mod, "_decrypt_quarantine_raw", _boom("decrypt"))
    monkeypatch.setattr(quarantine_mod, "decompress", _boom("decompress"))
    monkeypatch.setattr(quarantine_mod, "compute_signature", _boom("compute_signature"))

    response = quarantine_mod.list_quarantine(_req(), page=1)
    assert response.status_code == 200
    html = response.body.decode("utf-8", errors="replace") if isinstance(response.body, bytes) else str(response.body)
    assert "No Sig" in html

    list_sql = next(s for s in captured if "FROM quarantined_emails" in s and "LIMIT" in s)
    assert "CASE WHEN signature IS NOT NULL THEN raw_email" in list_sql


# ---------------------------------------------------------------------------
# _delete_quarantined_from_mail_server_and_db: negative UID / folder quoting
# ---------------------------------------------------------------------------

def test_delete_quarantined_negative_uid_is_db_only(monkeypatch):
    deleted = []
    monkeypatch.setattr(quarantine_mod, "execute", lambda *a, **k: None)

    def fake_query(sql, params=None):
        if "DELETE FROM quarantined_emails" in sql:
            deleted.append(sql)
            return FakeResult([])
        if "FROM quarantined_emails" in sql and "WHERE id" in sql:
            return FakeResult([{"id": 3, "original_source": "acct",
                                "original_folder": "INBOX", "original_uid": -5}])
        if "FROM fetch_accounts" in sql:
            return FakeResult([{"name": "acct", "host": "h", "port": 993,
                                "username": "u", "password_encrypted": "p",
                                "use_ssl": True, "require_starttls": False,
                                "account_type": "imap"}])
        raise AssertionError(f"Unexpected query: {str(sql)[:120]}")

    monkeypatch.setattr(quarantine_mod, "query", fake_query)
    monkeypatch.setattr(quarantine_mod, "IMAP4_SSL", _boom("IMAP4_SSL"))

    count, errors = quarantine_mod._delete_quarantined_from_mail_server_and_db([3])
    assert count == 1
    assert len(deleted) == 1
    assert any("no mail server copy" in e for e in errors)


def test_delete_quarantined_quotes_imap_folder(monkeypatch):
    monkeypatch.setattr(quarantine_mod, "execute", lambda *a, **k: None)
    calls = []

    def fake_query(sql, params=None):
        if "FROM quarantined_emails" in sql and "WHERE id" in sql:
            return FakeResult([{"id": 4, "original_source": "acct",
                                "original_folder": "Sent Items", "original_uid": 9}])
        if "FROM fetch_accounts" in sql:
            return FakeResult([{"name": "acct", "host": "h", "port": 993,
                                "username": "u", "password_encrypted": "p",
                                "use_ssl": True, "require_starttls": False,
                                "account_type": "imap"}])
        if "DELETE FROM quarantined_emails" in sql:
            return FakeResult([])
        raise AssertionError(f"Unexpected query: {str(sql)[:120]}")

    class FakeIMAP:
        def __init__(self, *a, **k):
            pass

        def login(self, *a, **k):
            return ("OK",)

        def select(self, folder):
            calls.append(folder)
            return ("OK", None)

        def uid(self, *a, **k):
            return ("OK", None)

        def expunge(self):
            return ("OK", None)

        def logout(self):
            return ("OK",)

    monkeypatch.setattr(quarantine_mod, "query", fake_query)
    monkeypatch.setattr(quarantine_mod, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setattr(quarantine_mod, "decrypt_password", lambda *a, **k: "pw")

    count, errors = quarantine_mod._delete_quarantined_from_mail_server_and_db([4])
    assert count == 1
    assert errors == []
    assert calls == ['"Sent Items"']  # quoted for IMAP
