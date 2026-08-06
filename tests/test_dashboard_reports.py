"""
Unit tests for the dashboard/reports audit fixes.

Covers:
- Reports endpoints that previously had their login checks commented out must
  now return 401 for unauthenticated requests.
- The AV-stats report derives "rejected" counts from the worker's reject log
  entries instead of hard-coding zero.
- The data-quality report's duplicate count is passed through correctly.
- get_user_date_format uses at most two queries and honours user overrides.
- save_dashboard_preferences normalizes (dedupes + clamps) submitted layouts.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import reports as reports_mod  # noqa: E402
from src.routes import dashboard as dashboard_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers: fake DB result + fake query dispatcher
# ---------------------------------------------------------------------------

class FakeResult:
    """Mimics the MaterializedResult surface used by the route code."""

    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


def fake_query_dispatch(handler):
    """Return a query() replacement that routes on SQL fragments."""

    def _query(sql, params=None):
        return handler(sql, params or {})

    return _query


def _req(user_id=None):
    session = {}
    if user_id is not None:
        session = {"user_id": user_id, "username": "tester"}
    return SimpleNamespace(session=session)


# ---------------------------------------------------------------------------
# Auth enforcement on previously-open report endpoints
# ---------------------------------------------------------------------------

def test_report_endpoints_require_login():
    """Every report endpoint rejects unauthenticated requests with 401."""
    endpoints = [
        (reports_mod.account_activity_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.av_stats_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.email_volume_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.system_health_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.storage_utilization_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.retention_policy_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.system_performance_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.security_access_report, {"start_date": "2026-01-01", "end_date": "2026-01-31"}),
        (reports_mod.data_quality_report, {}),
    ]
    for endpoint, kwargs in endpoints:
        response = endpoint(_req(), **kwargs)
        assert response.status_code == 401, f"{endpoint.__name__} did not enforce login"


# ---------------------------------------------------------------------------
# AV-stats: rejected counts come from worker log entries
# ---------------------------------------------------------------------------

def _av_rows():
    return [
        {"period_start": date(2026, 1, 1), "clean_count": 10, "quarantined_count": 2},
        {"period_start": date(2026, 1, 2), "clean_count": 5, "quarantined_count": 1},
    ]


def test_av_stats_uses_log_derived_rejected_counts(monkeypatch):
    def handler(sql, params):
        if "rejected due to virus" in sql:
            return FakeResult([{"rejected_date": date(2026, 1, 1), "c": 3}])
        if "total_clean" in sql:
            return FakeResult([{"total_clean": 15, "total_quarantined": 3}])
        if "clean_count" in sql:
            return FakeResult(_av_rows())
        raise AssertionError(f"Unexpected query: {sql[:80]}")

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))
    # Keep date/time formatting deterministic without touching the database.
    monkeypatch.setattr(reports_mod, "get_user_date_format", lambda request, date_only=False: "%d/%m/%Y")
    monkeypatch.setattr(
        reports_mod,
        "convert_utc_to_user_timezone",
        lambda value, user_id: value,
    )

    payload = reports_mod.av_stats_report(
        _req(user_id=1), start_date="2026-01-01", end_date="2026-01-31"
    )

    assert payload["rejected_emails"] == 3
    assert payload["rejected_counts"] == [3, 0]
    assert payload["clean_emails"] == 15
    assert payload["quarantined_emails"] == 3


# ---------------------------------------------------------------------------
# Data quality: duplicate count passes through
# ---------------------------------------------------------------------------

def test_data_quality_duplicate_count(monkeypatch):
    def handler(sql, params):
        if "potential_duplicates" in sql:
            return FakeResult([{"potential_duplicates": 3}])
        if "missing_subjects" in sql:
            return FakeResult([{
                "total_emails": 100, "missing_subjects": 0, "missing_senders": 0,
                "missing_recipients": 0, "unscanned_emails": 0, "virus_detected": 0,
            }])
        if "under_1kb" in sql:
            return FakeResult([{
                "under_1kb": 10, "kb_1_10": 10, "kb_10_100": 10,
                "kb_100_1024": 10, "over_1mb": 10,
            }])
        if "error_count" in sql:
            return FakeResult([{"error_count": 1, "warning_count": 0, "total_logs": 10}])
        raise AssertionError(f"Unexpected query: {sql[:80]}")

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))

    payload = reports_mod.data_quality_report(_req(user_id=1))
    assert payload["potential_duplicates"] == 3


# ---------------------------------------------------------------------------
# get_user_date_format: at most two queries, user override wins
# ---------------------------------------------------------------------------

def test_get_user_date_format_two_queries_with_user_override(monkeypatch):
    calls = []

    def handler(sql, params):
        calls.append(sql)
        if "settings" in sql:
            return FakeResult([
                {"key": "date_format", "value": "%Y-%m-%d"},
                {"key": "time_format", "value": "%H:%M"},
            ])
        if "FROM users" in sql:
            return FakeResult([{"date_format": "%d %b %Y", "time_format": None}])
        return FakeResult([])

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))

    fmt = reports_mod.get_user_date_format(_req(user_id=7), date_only=True)
    assert fmt == "%d %b %Y"  # user override beats global
    assert len(calls) == 2  # one settings query + one user query per call

    fmt_full = reports_mod.get_user_date_format(_req(user_id=7))
    assert fmt_full == "%d %b %Y %H:%M"  # global time format retained
    assert len(calls) == 4  # still two queries per call


def test_get_user_date_format_global_only_when_no_user(monkeypatch):
    calls = []

    def handler(sql, params):
        calls.append(sql)
        if "settings" in sql:
            return FakeResult([
                {"key": "date_format", "value": "%d/%m/%Y"},
                {"key": "time_format", "value": "%I:%M %p"},
            ])
        return FakeResult([])

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))

    fmt = reports_mod.get_user_date_format(_req())
    assert fmt == "%d/%m/%Y %I:%M %p"
    assert len(calls) == 1  # no user query when not logged in


# ---------------------------------------------------------------------------
# Dashboard layout normalization (dedupe + clamp)
# ---------------------------------------------------------------------------

def test_normalize_dashboard_layout_dedupes_and_clamps():
    layout = dashboard_mod.DashboardLayout(widgets=[
        {"widget_id": "total-emails", "x": 0, "y": 0, "w": 3, "h": 3, "visible": True},
        # Duplicate widget id must be dropped (would violate the unique constraint)
        {"widget_id": "total-emails", "x": 9, "y": 9, "w": 6, "h": 6, "visible": False},
        # Out-of-range values must be clamped, not stored as-is
        {"widget_id": "storage-used", "x": -5, "y": 99999, "w": 0, "h": 200000, "visible": True},
    ])

    normalized = dashboard_mod._normalize_dashboard_layout(layout)

    assert len(normalized) == 2
    assert normalized[0].widget_id == "total-emails"
    assert normalized[1].widget_id == "storage-used"
    assert normalized[1].x == 0
    assert normalized[1].y == 1000
    assert normalized[1].w == 1
    assert normalized[1].h == 1000


def test_normalize_dashboard_layout_limits_widget_count():
    widgets = [
        {"widget_id": f"w-{i}", "x": 0, "y": 0, "w": 3, "h": 3, "visible": True}
        for i in range(100)
    ]
    layout = dashboard_mod.DashboardLayout(widgets=widgets)
    normalized = dashboard_mod._normalize_dashboard_layout(layout)
    assert len(normalized) == 64


def test_reports_charts_fill_their_containers():
    """All dynamically-created report charts must use the full card width."""
    template = (API_DIR / "templates" / "reports.html").read_text(encoding="utf-8")
    styles = (API_DIR / "static" / "styles.css").read_text(encoding="utf-8")

    # The performance charts are created after the initial chart setup, so they
    # need the same explicit sizing policy as the charts initialized on load.
    assert "text: 'Processing Performance Over Time'" in template
    assert "text: 'Worker Activity Distribution'" in template
    storage_start = template.index("function loadStorageUtilizationReport()")
    storage_end = template.index("// Load Retention Policy Report", storage_start)
    storage_script = template[storage_start:storage_end]
    assert "text: 'Storage Usage Over Time'" in storage_script
    assert "maintainAspectRatio: false" in storage_script
    assert template.count("maintainAspectRatio: false") >= 10
    performance_start = template.index("function loadSystemPerformanceReport()")
    performance_end = template.index("// Load Security & Access Report", performance_start)
    performance_script = template[performance_start:performance_end]
    assert performance_script.count("maintainAspectRatio: false") == 2
    assert ".chart-container > canvas" in styles
    assert "width: 100% !important" in styles
