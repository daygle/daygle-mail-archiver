"""Regression tests for the Logs page and best-effort logger."""

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

from src.routes import logs as logs_mod  # noqa: E402
from src.utils import logger as logger_mod  # noqa: E402


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def mappings(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _Request:
    def __init__(self, user_id=1):
        self.session = {"user_id": user_id, "username": "auditor"}
        self.scope = {"session": self.session}


def _render(monkeypatch, **kwargs):
    queries = []

    def fake_query(sql, params=None):
        queries.append((sql, params or {}))
        if "page_size FROM users" in sql:
            return _Result([{"page_size": "not-a-number"}])
        if "key = 'page_size'" in sql:
            return _Result([{"value": "also-invalid"}])
        if "COUNT(*)" in sql:
            return _Result([{"total": 1}])
        if "SELECT DISTINCT source" in sql:
            return _Result([{"source": "Auth"}])
        return _Result([{
            "id": 9,
            "timestamp": None,
            "level": "info",
            "source": "Auth",
            "message": "hello",
            "details": None,
        }])

    monkeypatch.setattr(logs_mod, "query", fake_query)
    monkeypatch.setattr(
        logs_mod.templates,
        "TemplateResponse",
        lambda name, context: context,
    )
    return logs_mod.logs(_Request(), **kwargs), queries


def test_logs_validates_dates_and_falls_back_from_bad_page_size(monkeypatch):
    context, queries = _render(
        monkeypatch,
        date_from="not-a-date",
        date_to="2026-02-30",
        search="  error & warning  ",
        page=999,
    )

    assert context["page_size"] == 50
    assert context["current_date_from"] == ""
    assert context["current_date_to"] == ""
    assert context["current_search"] == "error & warning"
    data_queries = [params for sql, params in queries if "FROM logs" in sql]
    assert all("date_from" not in params and "date_to" not in params for params in data_queries)
    assert context["page"] == 1


def test_logs_uses_typed_date_filters_and_deterministic_ordering(monkeypatch):
    context, queries = _render(
        monkeypatch,
        date_from="2026-01-01",
        date_to="2026-01-31",
        level="WARNING",
        source="Auth",
    )

    assert context["current_level"] == "warning"
    data_query = next(sql for sql, _ in queries if "SELECT id, timestamp" in sql)
    data_params = next(params for sql, params in queries if "SELECT id, timestamp" in sql)
    assert "ORDER BY timestamp DESC, id DESC" in data_query
    assert data_params["date_from"].isoformat() == "2026-01-01"
    assert data_params["date_to"].isoformat() == "2026-01-31"


def test_logger_does_not_raise_when_log_database_is_unavailable(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(logger_mod, "query", fail)
    assert logger_mod.log("error", "Tests", "message") is False


def test_logger_normalizes_levels_and_bounds_values(monkeypatch):
    captured = {}

    def save(sql, params):
        captured.update(params)

    monkeypatch.setattr(logger_mod, "query", save)
    assert logger_mod.log("unexpected", "S" * 500, "M" * 600, "D" * 5000) is True
    assert captured["level"] == "info"
    assert len(captured["source"]) == 200
    assert len(captured["message"]) == 500
    assert len(captured["details"]) == 4000
