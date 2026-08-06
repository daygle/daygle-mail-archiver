"""Tests for Alert Management route behavior and trigger integration."""

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

from src.routes import alert_management as alerts_mod  # noqa: E402
from src.utils import alerts as alerts_core  # noqa: E402
from src.utils.permissions import PermissionChecker  # noqa: E402


class _Result:
    def __init__(self, rowcount=1, row=None, rows=None):
        self.rowcount = rowcount
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def mappings(self):
        return self

    def first(self):
        return self._row

    def all(self):
        return self._rows


class _ReturningResult:
    def fetchone(self):
        return (42,)


class _URL:
    path = "/alert-management"


def _request(perms=("manage_alerts",)):
    session = {"user_id": 1, "username": "admin", "permissions": list(perms)}
    return SimpleNamespace(session=session, scope={"session": session}, url=_URL())


def _grant(monkeypatch, perms=("manage_alerts",)):
    monkeypatch.setattr(PermissionChecker, "_load_user_permissions", lambda self: list(perms))


def test_alert_creation_skips_disabled_trigger(monkeypatch):
    executed = []
    monkeypatch.setattr(alerts_core, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        alerts_core,
        "query",
        lambda *args, **kwargs: _Result(row={"enabled": False, "alert_type": "error"}),
    )
    monkeypatch.setattr(
        alerts_core,
        "execute",
        lambda *args, **kwargs: executed.append(args) or _ReturningResult(),
    )

    result = alerts_core.create_alert(
        "warning", "Title", "Message", trigger_key="virus_detected"
    )

    assert result == 0
    assert executed == []


def test_alert_creation_uses_configured_trigger_severity(monkeypatch):
    executed = []
    monkeypatch.setattr(alerts_core, "log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        alerts_core,
        "query",
        lambda *args, **kwargs: _Result(row={"enabled": True, "alert_type": "error"}),
    )
    monkeypatch.setattr(
        alerts_core,
        "execute",
        lambda sql, params=None: executed.append((sql, params)) or _ReturningResult(),
    )
    monkeypatch.setattr(alerts_core, "_send_alert_email", lambda *args, **kwargs: None)

    result = alerts_core.create_alert(
        "info", "Title", "Message", send_email=False, trigger_key="virus_detected"
    )

    assert result == 42
    assert executed[0][1]["alert_type"] == "error"


def test_update_status_rejects_unknown_trigger(monkeypatch):
    _grant(monkeypatch)
    monkeypatch.setattr(alerts_mod, "execute", lambda *args, **kwargs: _Result(rowcount=0))
    monkeypatch.setattr(alerts_mod, "query", lambda *args, **kwargs: _Result(row=None))
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)

    request = _request()
    response = alerts_mod.update_trigger_status(request, trigger_id=9999, enabled=True)

    assert response.status_code == 303
    assert request.session["flash"]["message"] == "Alert trigger not found."


def test_update_status_succeeds_and_uses_trigger_name(monkeypatch):
    _grant(monkeypatch)
    calls = []
    monkeypatch.setattr(
        alerts_mod,
        "query",
        lambda *args, **kwargs: _Result(row={"name": "Virus Detected"}),
    )
    monkeypatch.setattr(
        alerts_mod,
        "execute",
        lambda sql, params=None: calls.append((sql, params)) or _Result(),
    )
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)
    request = _request()

    response = alerts_mod.update_trigger_status(request, trigger_id=7, enabled=False)

    assert response.status_code == 303
    assert calls[0][1] == {"enabled": False, "id": 7}
    assert request.session["flash"]["type"] == "success"
    assert "Virus Detected" in request.session["flash"]["message"]


def test_update_severity_succeeds_and_uses_trigger_name(monkeypatch):
    _grant(monkeypatch)
    calls = []
    monkeypatch.setattr(
        alerts_mod,
        "query",
        lambda *args, **kwargs: _Result(row={"name": "Virus Detected"}),
    )
    monkeypatch.setattr(
        alerts_mod,
        "execute",
        lambda sql, params=None: calls.append((sql, params)) or _Result(),
    )
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)
    request = _request()

    response = alerts_mod.update_trigger_severity(request, trigger_id=7, alert_type="warning")

    assert response.status_code == 303
    assert calls[0][1] == {"alert_type": "warning", "id": 7}
    assert request.session["flash"]["type"] == "success"
    assert "Virus Detected" in request.session["flash"]["message"]


def test_update_status_handles_update_race(monkeypatch):
    _grant(monkeypatch)
    monkeypatch.setattr(alerts_mod, "query", lambda *args, **kwargs: _Result(row={"name": "Virus Detected"}))
    monkeypatch.setattr(alerts_mod, "execute", lambda *args, **kwargs: _Result(rowcount=0))
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)
    request = _request()

    response = alerts_mod.update_trigger_status(request, trigger_id=7, enabled=False)

    assert response.status_code == 303
    assert request.session["flash"]["message"] == "Alert trigger no longer exists."


def test_update_status_database_failure_is_reported(monkeypatch):
    _grant(monkeypatch)
    monkeypatch.setattr(
        alerts_mod,
        "query",
        lambda *args, **kwargs: _Result(row={"name": "Virus Detected"}),
    )
    monkeypatch.setattr(alerts_mod, "execute", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db down")))
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)
    request = _request()

    response = alerts_mod.update_trigger_status(request, trigger_id=7, enabled=False)

    assert response.status_code == 303
    assert request.session["flash"]["type"] == "error"


def test_update_severity_rejects_invalid_type(monkeypatch):
    _grant(monkeypatch)
    request = _request()

    response = alerts_mod.update_trigger_severity(
        request, trigger_id=1, alert_type="critical"
    )

    assert response.status_code == 303
    assert request.session["flash"] == {
        "message": "Invalid alert type: critical",
        "type": "error",
    }


def test_update_severity_rejects_unknown_trigger(monkeypatch):
    _grant(monkeypatch)
    monkeypatch.setattr(alerts_mod, "execute", lambda *args, **kwargs: _Result(rowcount=0))
    monkeypatch.setattr(alerts_mod, "query", lambda *args, **kwargs: _Result(row=None))
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)
    request = _request()

    response = alerts_mod.update_trigger_severity(request, trigger_id=9999, alert_type="warning")

    assert response.status_code == 303
    assert request.session["flash"]["message"] == "Alert trigger not found."


def test_management_page_surfaces_database_failure(monkeypatch):
    _grant(monkeypatch)
    monkeypatch.setattr(alerts_mod, "get_unacknowledged_count", lambda: 0, raising=False)
    monkeypatch.setattr(alerts_mod, "log", lambda *args, **kwargs: None)

    def fail_query(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(alerts_mod, "query", fail_query)
    request = _request()

    response = alerts_mod.alert_management_form(request)

    assert response.status_code == 200
    assert "Unable to load alert triggers" in response.body.decode()
