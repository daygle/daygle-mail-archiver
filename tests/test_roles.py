"""
Unit tests for the role-management privilege guards.

These cover the escalation-prevention logic added to the RBAC layer: a user may
only grant (to a role) or assign (to a user) privileged permissions they already
hold themselves. No database is required; the module-level ``query`` and
``PermissionChecker`` are monkeypatched.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

# Make the api package importable and satisfy the module-level config lookups.
API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault(
    "DB_DSN",
    "postgresql+psycopg2://test:test@localhost:5432/test",
)
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.utils import permissions as perm  # noqa: E402
from src.utils.db import MaterializedResult  # noqa: E402

# ---------------------------------------------------------------------------
# Fake data (mirrors db/schema.sql seed)
# ---------------------------------------------------------------------------

PERMISSION_ROWS = [
    {"id": 1, "name": "view_dashboard"},
    {"id": 2, "name": "view_emails"},
    {"id": 3, "name": "manage_users"},
    {"id": 4, "name": "manage_roles"},
    {"id": 5, "name": "manage_global_settings"},
    {"id": 6, "name": "view_users"},
    {"id": 7, "name": "view_quarantine"},
]

ROLE_ROWS = [{"id": 1}, {"id": 2}, {"id": 3}]

# role_id -> permission names (mirrors the seeded roles)
ROLE_PERMISSION_ROWS = [
    {"role_id": 1, "name": "view_dashboard"},   # administrator
    {"role_id": 1, "name": "manage_users"},
    {"role_id": 1, "name": "manage_roles"},
    {"role_id": 1, "name": "manage_global_settings"},
    {"role_id": 2, "name": "view_dashboard"},   # read_only
    {"role_id": 2, "name": "view_emails"},
    {"role_id": 3, "name": "view_dashboard"},   # user_manager
    {"role_id": 3, "name": "view_users"},
    {"role_id": 3, "name": "manage_users"},
    {"role_id": 3, "name": "manage_roles"},
]

ACTOR_PERMISSIONS = []  # set per-test


class FakePermissionChecker:
    def __init__(self, request):
        self.request = request

    def get_user_permissions(self):
        return list(ACTOR_PERMISSIONS)


class FakeRequest:
    def __init__(self):
        self.session = {}


@pytest.fixture(autouse=True)
def _patch_db(monkeypatch):
    """Route perm.query to the fake in-memory data and swap the checker."""
    ACTOR_PERMISSIONS.clear()

    def fake_query(sql, params=None):
        params = params or {}
        if "FROM permissions" in sql and "role_permissions" not in sql:
            return MaterializedResult([dict(r) for r in PERMISSION_ROWS])
        if "FROM roles" in sql and "role_permissions" not in sql:
            return MaterializedResult([dict(r) for r in ROLE_ROWS])
        if "role_permissions" in sql:
            return MaterializedResult([dict(r) for r in ROLE_PERMISSION_ROWS])
        return MaterializedResult([])

    monkeypatch.setattr(perm, "query", fake_query)
    monkeypatch.setattr(perm, "PermissionChecker", FakePermissionChecker)


# ---------------------------------------------------------------------------
# check_privileged_grant (adding permissions to a role)
# ---------------------------------------------------------------------------


def test_grant_allows_permissions_actor_holds():
    ACTOR_PERMISSIONS.extend(["manage_users", "manage_roles"])
    # manage_users + manage_roles are held; view_emails is non-privileged
    assert perm.check_privileged_grant(FakeRequest(), [3, 4, 2]) is None


def test_grant_blocks_privileged_permission_actor_lacks():
    ACTOR_PERMISSIONS.extend(["manage_users"])
    error = perm.check_privileged_grant(FakeRequest(), [3, 5])
    assert error is not None
    assert "manage_global_settings" in error
    assert "manage_users" not in error  # held, so not reported


def test_grant_allows_non_privileged_permissions_to_anyone():
    ACTOR_PERMISSIONS.extend(["view_dashboard"])
    assert perm.check_privileged_grant(FakeRequest(), [2, 7]) is None


def test_grant_empty_is_allowed():
    assert perm.check_privileged_grant(FakeRequest(), []) is None


def test_grant_rejects_non_numeric_ids():
    assert perm.check_privileged_grant(FakeRequest(), ["abc"]) is not None
    assert perm.check_privileged_grant(FakeRequest(), [None]) is not None


def test_grant_rejects_unknown_permission_ids():
    """A stale/forged permission id must fail up front, not via FK violation."""
    ACTOR_PERMISSIONS.extend(["manage_users"])
    error = perm.check_privileged_grant(FakeRequest(), [999])
    assert error is not None
    assert "999" in error


# ---------------------------------------------------------------------------
# check_privileged_role_assignment (assigning roles to a user)
# ---------------------------------------------------------------------------


def test_assign_blocks_administrator_role_for_user_manager():
    # user_manager (role 3) lacks manage_global_settings, which administrator
    # (role 1) carries -> must be blocked (this was the self-escalation path)
    ACTOR_PERMISSIONS.extend(["view_dashboard", "view_users", "manage_users", "manage_roles"])
    error = perm.check_privileged_role_assignment(FakeRequest(), [1])
    assert error is not None
    assert "manage_global_settings" in error


def test_assign_allows_same_power_role_for_user_manager():
    # Granting the user_manager role (3) is fine: everything it carries is held
    ACTOR_PERMISSIONS.extend(["view_dashboard", "view_users", "manage_users", "manage_roles"])
    assert perm.check_privileged_role_assignment(FakeRequest(), [3]) is None


def test_assign_allows_administrator_for_administrator():
    ACTOR_PERMISSIONS.extend(
        ["view_dashboard", "manage_users", "manage_roles", "manage_global_settings", "view_users"]
    )
    assert perm.check_privileged_role_assignment(FakeRequest(), [1, 3]) is None


def test_assign_allows_unprivileged_roles_for_anyone():
    ACTOR_PERMISSIONS.extend(["view_dashboard"])
    assert perm.check_privileged_role_assignment(FakeRequest(), [2]) is None  # read_only


def test_assign_rejects_empty_and_invalid():
    assert perm.check_privileged_role_assignment(FakeRequest(), []) is not None
    assert perm.check_privileged_role_assignment(FakeRequest(), ["x"]) is not None


def test_assign_rejects_unknown_role_ids():
    """A stale/forged role id must fail up front, not via FK violation."""
    ACTOR_PERMISSIONS.extend(["view_dashboard"])
    error = perm.check_privileged_role_assignment(FakeRequest(), [99])
    assert error is not None
    assert "99" in error


# ---------------------------------------------------------------------------
# Fail-closed behaviour
# ---------------------------------------------------------------------------


def test_roles_page_uses_single_column_role_layout():
    css = (API_DIR / "static" / "roles.css").read_text(encoding="utf-8")
    assert ".role-grid" in css
    assert "grid-template-columns: 1fr" in css
    assert "repeat(auto-fill" not in css


def test_schema_seeds_manual_scan_permission_for_email_manager():
    schema = (API_DIR.parent / "db" / "schema.sql").read_text(encoding="utf-8")
    assert "('scan_emails', 'Run ClamAV scans on archived emails', 'emails')" in schema
    email_manager_start = schema.index("WHERE r.name = 'email_manager'")
    email_manager_end = schema.index("-- Auditor permissions", email_manager_start)
    assert "'scan_emails'" in schema[email_manager_start:email_manager_end]


def test_grant_fails_closed_on_db_error(monkeypatch):
    def boom(sql, params=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(perm, "query", boom)
    ACTOR_PERMISSIONS.extend(["manage_users"])
    assert perm.check_privileged_grant(FakeRequest(), [3]) is not None
    assert perm.check_privileged_role_assignment(FakeRequest(), [1]) is not None
