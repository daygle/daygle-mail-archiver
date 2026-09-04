"""
Tests for per-user table column visibility preferences.

Covers the shared helpers in ``src/utils/table_prefs`` and the
``/api/user/table-columns`` endpoints in ``src/routes/profile``.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import profile as profile_mod  # noqa: E402
from src.utils import table_prefs as prefs_mod  # noqa: E402


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


def _req(user_id=1):
    session = {"user_id": user_id, "username": "tester"} if user_id is not None else {}
    return SimpleNamespace(session=session, scope={"session": session})


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def test_get_hidden_columns_empty_without_user_or_page(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("query must not run for these inputs")

    monkeypatch.setattr(prefs_mod, "query", _boom)
    assert prefs_mod.get_hidden_columns(None, "emails") == []
    assert prefs_mod.get_hidden_columns(1, "unknown-page") == []


def test_get_hidden_columns_filters_to_known_columns(monkeypatch):
    monkeypatch.setattr(
        prefs_mod,
        "query",
        lambda sql, params=None: FakeResult([{"hidden_columns": {"emails": ["to", "bogus", "folder"]}}]),
    )
    assert prefs_mod.get_hidden_columns(1, "emails") == ["to", "folder"]


def test_get_hidden_columns_survives_db_errors(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prefs_mod, "query", _boom)
    assert prefs_mod.get_hidden_columns(1, "emails") == []


def test_save_hidden_columns_read_modify_write_keeps_other_pages(monkeypatch):
    calls = []

    def fake_query(sql, params=None):
        calls.append((sql, params))
        if "SELECT hidden_columns" in sql:
            return FakeResult([{"hidden_columns": {"quarantine": ["account"]}}])
        return FakeResult([])

    monkeypatch.setattr(prefs_mod, "query", fake_query)
    assert prefs_mod.save_hidden_columns(1, "emails", ["folder"]) is True

    # The upsert must carry BOTH pages' keys so emails/quarantine never clobber.
    upsert_sql, upsert_params = calls[-1]
    assert "ON CONFLICT (user_id)" in upsert_sql
    assert '"emails": ["folder"]' in upsert_params["hidden"]
    assert '"quarantine": ["account"]' in upsert_params["hidden"]


def test_save_hidden_columns_rejects_invalid_input(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("query must not run for these inputs")

    monkeypatch.setattr(prefs_mod, "query", _boom)
    assert prefs_mod.save_hidden_columns(None, "emails", ["to"]) is False
    assert prefs_mod.save_hidden_columns(1, "nope", ["to"]) is False
    assert prefs_mod.save_hidden_columns(1, "emails", "not-a-list") is False


def test_save_hidden_columns_filters_unknown_and_survives_db_errors(monkeypatch):
    monkeypatch.setattr(prefs_mod, "query", lambda sql, params=None: FakeResult([]))
    assert prefs_mod.save_hidden_columns(1, "quarantine", ["account", "bogus"]) is True
    # 'bogus' was dropped before persistence; a follow-up read confirms it.
    assert prefs_mod.get_hidden_columns(1, "quarantine") == []


def test_save_hidden_columns_db_failure_returns_false(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(prefs_mod, "query", _boom)
    assert prefs_mod.save_hidden_columns(1, "emails", ["to"]) is False


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_get_table_columns_requires_login():
    assert profile_mod.get_table_columns(_req(user_id=None), page="emails").status_code == 401


def test_get_table_columns_rejects_unknown_page():
    assert profile_mod.get_table_columns(_req(), page="nope").status_code == 400


def test_get_table_columns_returns_saved_hidden(monkeypatch):
    monkeypatch.setattr(
        prefs_mod,
        "query",
        lambda sql, params=None: FakeResult([{"hidden_columns": {"emails": ["to"]}}]),
    )
    payload = profile_mod.get_table_columns(_req(), page="emails")
    assert payload["page"] == "emails"
    assert payload["hidden"] == ["to"]


def test_save_table_columns_requires_login():
    response = profile_mod.save_table_columns(
        _req(user_id=None), {"page": "emails", "hidden": ["to"]}
    )
    assert response.status_code == 401


def test_save_table_columns_rejects_unknown_page(monkeypatch):
    monkeypatch.setattr(prefs_mod, "query", lambda sql, params=None: FakeResult([]))
    response = profile_mod.save_table_columns(_req(), {"page": "nope", "hidden": []})
    assert response.status_code == 400


def test_save_table_columns_persists_and_echoes(monkeypatch):
    saved = []
    store = {}  # persisted hidden_columns dict, so the echo re-read sees it

    def fake_query(sql, params=None):
        saved.append(params)
        if "SELECT hidden_columns" in sql:
            return FakeResult([{"hidden_columns": dict(store)}])
        if "INSERT INTO user_table_prefs" in sql:
            store.update(__import__("json").loads(params["hidden"]))
        return FakeResult([])

    monkeypatch.setattr(prefs_mod, "query", fake_query)
    response = profile_mod.save_table_columns(
        _req(), {"page": "emails", "hidden": ["to", "folder"]}
    )
    assert response.status_code == 200
    import json as _json
    body = _json.loads(response.body.decode("utf-8"))
    assert body["status"] == "ok"
    assert body["hidden"] == ["to", "folder"]
    assert saved[-1]["user_id"] == 1