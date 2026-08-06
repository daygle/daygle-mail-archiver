"""Tests for the structured Role Management page data."""

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

from src.routes import roles as roles_mod  # noqa: E402


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


def test_list_roles_builds_structured_permissions_and_counts(monkeypatch):
    calls = []

    def fake_query(sql, params=None):
        calls.append(sql)
        if "COUNT(DISTINCT rp.permission_id)" in sql:
            return _Result([{
                "id": 1,
                "name": "auditor",
                "display_name": "Auditor",
                "description": "Reviews system activity",
                "is_system_role": True,
                "permission_count": 2,
                "user_count": 3,
            }])
        if "SELECT id, name, description, category" in sql:
            return _Result([
                {"id": 10, "name": "view_logs", "description": "View logs", "category": "system"},
                {"id": 11, "name": "view_reports", "description": "View reports", "category": "reports"},
            ])
        if "SELECT rp.role_id, p.name" in sql:
            return _Result([
                {"role_id": 1, "name": "view_logs"},
                {"role_id": 1, "name": "view_reports"},
            ])
        raise AssertionError(f"Unexpected query: {sql[:100]}")

    captured = {}
    monkeypatch.setattr(roles_mod, "query", fake_query)
    monkeypatch.setattr(
        roles_mod.templates,
        "TemplateResponse",
        lambda name, context: captured.update({"name": name, "context": context}) or context,
    )

    request = SimpleNamespace(session={})
    result = roles_mod.list_roles(request)

    assert result is captured["context"]
    assert captured["name"] == "roles.html"
    role = captured["context"]["roles"][0]
    assert role["permissions"] == ["view_logs", "view_reports"]
    assert role["permission_count"] == 2
    assert role["user_count"] == 3
    assert captured["context"]["permissions"][0]["category"] == "system"
    assert len(calls) == 3
