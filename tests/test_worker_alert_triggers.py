"""Unit tests for the worker's TTL-cached alert-trigger lookups.

``create_alert(..., trigger_key=...)`` resolves the trigger's enabled flag and
configured severity once per TTL interval instead of once per email (virus
detection in worker.py and ClamAV scan errors in clamav_scanner.py both fire
per message). The shared cache in ``shared/alert_triggers.py`` is exercised
directly, and both worker implementations are covered end to end with a mocked
database layer; no network or database required.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import shared.alert_triggers as alert_triggers  # noqa: E402

WORKER_SRC = REPO_ROOT / "worker" / "src"
sys.path.insert(0, str(WORKER_SRC))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())

import worker  # noqa: E402
import clamav_scanner  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_cache():
    """Isolate the module-level TTL cache between tests."""
    alert_triggers._cache.clear()
    yield
    alert_triggers._cache.clear()


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


class _QueryRecorder:
    """Answers alert_triggers lookups and counts how many actually ran."""

    def __init__(self, trigger_row):
        self._trigger_row = trigger_row  # None => no row exists
        self.trigger_queries = 0
        self.executed = []  # (sql, params) from the fake execute

    def __call__(self, sql, params=None):
        if "alert_triggers" in sql:
            self.trigger_queries += 1
            return _Row(self._trigger_row) if self._trigger_row is not None else _NoRow()
        return _NoRow()

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


# ---------------------------------------------------------------------------
# shared/alert_triggers.py (cache semantics)
# ---------------------------------------------------------------------------


def test_lookup_cached_within_ttl(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(alert_triggers.time, "monotonic", lambda: clock[0])
    fake = _QueryRecorder({"alert_type": "warning", "enabled": True})

    assert alert_triggers.get_alert_trigger("k1", query=fake) == (True, "warning")
    assert alert_triggers.get_alert_trigger("k1", query=fake) == (True, "warning")
    assert fake.trigger_queries == 1

    clock[0] += alert_triggers._CACHE_TTL_SECONDS + 1
    assert alert_triggers.get_alert_trigger("k1", query=fake) == (True, "warning")
    assert fake.trigger_queries == 2


def test_missing_row_cached_as_none(monkeypatch):
    fake = _QueryRecorder(None)
    assert alert_triggers.get_alert_trigger("k2", query=fake) is None
    assert alert_triggers.get_alert_trigger("k2", query=fake) is None
    assert fake.trigger_queries == 1


def test_no_query_hook_returns_none_without_error():
    assert alert_triggers.get_alert_trigger("k3") is None


# ---------------------------------------------------------------------------
# worker.create_alert (virus_detected path, once per infected email)
# ---------------------------------------------------------------------------


def test_create_alert_collapses_per_email_lookups(monkeypatch):
    """20 per-email alert creations => exactly 1 alert_triggers query."""
    fake = _QueryRecorder({"alert_type": "warning", "enabled": True})
    monkeypatch.setattr(worker, "query", fake)
    monkeypatch.setattr(worker, "execute", fake.execute)

    for _ in range(20):
        worker.create_alert("error", "t", "m", trigger_key="virus_detected")

    assert fake.trigger_queries == 1
    assert len(fake.executed) == 20
    assert all(p["alert_type"] == "warning" for _, p in fake.executed)


def test_create_alert_disabled_trigger_skips(monkeypatch):
    fake = _QueryRecorder({"alert_type": "warning", "enabled": False})
    monkeypatch.setattr(worker, "query", fake)
    monkeypatch.setattr(worker, "execute", fake.execute)

    worker.create_alert("error", "t", "m", trigger_key="virus_detected")
    assert fake.executed == []


def test_create_alert_unknown_trigger_falls_back(monkeypatch):
    fake = _QueryRecorder(None)
    monkeypatch.setattr(worker, "query", fake)
    monkeypatch.setattr(worker, "execute", fake.execute)

    worker.create_alert("error", "t", "m", trigger_key="virus_detected")
    assert len(fake.executed) == 1
    assert fake.executed[0][1]["alert_type"] == "error"


def test_create_alert_without_trigger_key_skips_lookup(monkeypatch):
    fake = _QueryRecorder({"alert_type": "warning", "enabled": True})
    monkeypatch.setattr(worker, "query", fake)
    monkeypatch.setattr(worker, "execute", fake.execute)

    worker.create_alert("info", "t", "m")
    assert fake.trigger_queries == 0
    assert fake.executed[0][1]["alert_type"] == "info"


def test_create_alert_query_failure_is_not_cached(monkeypatch):
    calls = {"n": 0}

    def failing_query(sql, params=None):
        calls["n"] += 1
        raise RuntimeError("db down")

    monkeypatch.setattr(worker, "query", failing_query)
    monkeypatch.setattr(worker, "execute", lambda sql, params=None: None)

    worker.create_alert("error", "t", "m", trigger_key="virus_detected")
    worker.create_alert("error", "t", "m", trigger_key="virus_detected")
    assert calls["n"] == 2  # transient failures retry on every call


# ---------------------------------------------------------------------------
# clamav_scanner.create_alert (scan-error path, once per failing email)
# ---------------------------------------------------------------------------


def test_clamav_create_alert_uses_configured_type(monkeypatch):
    fake = _QueryRecorder({"alert_type": "warning", "enabled": True})
    monkeypatch.setattr(clamav_scanner, "query", fake)
    monkeypatch.setattr(clamav_scanner, "execute", fake.execute)

    clamav_scanner.create_alert("error", "t", "m", trigger_key="clamav_error")
    assert len(fake.executed) == 1
    assert fake.executed[0][1]["alert_type"] == "warning"


def test_clamav_create_alert_disabled_skips(monkeypatch):
    fake = _QueryRecorder({"alert_type": "warning", "enabled": False})
    monkeypatch.setattr(clamav_scanner, "query", fake)
    monkeypatch.setattr(clamav_scanner, "execute", fake.execute)

    clamav_scanner.create_alert("error", "t", "m", trigger_key="clamav_error")
    assert fake.executed == []