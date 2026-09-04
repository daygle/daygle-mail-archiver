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

from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import reports as reports_mod  # noqa: E402
from src.routes import dashboard as dashboard_mod  # noqa: E402
from src.utils import timezone as timezone_mod  # noqa: E402


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
        # av_stats_report resolves the user timezone once per request, which
        # queries the users table and falls back to the global setting.
        if "FROM users" in sql or "key = 'timezone'" in sql:
            return FakeResult([])
        raise AssertionError(f"Unexpected query: {sql[:80]}")

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))
    # Keep date/time formatting deterministic without touching the database.
    # The report endpoint resolves the user timezone once per request (and
    # threads it through as tz=), so stub that hoisted lookup too.
    monkeypatch.setattr(reports_mod, "get_user_timezone", lambda user_id: "UTC")
    monkeypatch.setattr(reports_mod, "get_user_date_format", lambda request, date_only=False: "%d/%m/%Y")
    monkeypatch.setattr(
        reports_mod,
        "convert_utc_to_user_timezone",
        # The report endpoint resolves the user timezone once and passes it
        # through as tz=; the fake absorbs the keyword.
        lambda value, user_id, **kwargs: value,
    )

    payload = reports_mod.av_stats_report(
        _req(user_id=1), start_date="2026-01-01", end_date="2026-01-31"
    )

    assert payload["rejected_emails"] == 3
    assert payload["rejected_counts"] == [3, 0]
    assert payload["clean_emails"] == 15
    assert payload["quarantined_emails"] == 3


# ---------------------------------------------------------------------------
# Chart buckets and labels are user-local (no boundary conversion)
# ---------------------------------------------------------------------------

def test_email_volume_buckets_in_user_timezone(monkeypatch):
    captured = {}

    def handler(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return FakeResult([
            {"period_start": date(2026, 1, 15), "email_count": 4, "virus_count": 1, "sources_count": 2},
        ])

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))
    monkeypatch.setattr(reports_mod, "get_user_timezone", lambda user_id: "America/Los_Angeles")
    monkeypatch.setattr(reports_mod, "get_user_date_format", lambda request, date_only=False: "%d/%m/%Y")

    reports_mod.email_volume_report(
        _req(user_id=1), start_date="2026-01-15", end_date="2026-01-15"
    )

    # The SQL must mark the naive UTC column and express it in the user's tz
    # before truncating, and pass the resolved timezone as a bind parameter.
    assert "AT TIME ZONE 'UTC'" in captured["sql"]
    assert "AT TIME ZONE :tz" in captured["sql"]
    assert "DATE(created_at)" not in captured["sql"]
    assert captured["params"]["tz"] == "America/Los_Angeles"


def test_email_volume_labels_are_bucket_starts_not_converted_boundaries(monkeypatch):
    def handler(sql, params):
        if "GROUP BY" in sql:
            return FakeResult([
                {"period_start": date(2026, 1, 15), "email_count": 4, "virus_count": 1, "sources_count": 2},
            ])
        raise AssertionError(f"Unexpected query: {sql[:80]}")

    monkeypatch.setattr(reports_mod, "query", fake_query_dispatch(handler))
    monkeypatch.setattr(reports_mod, "get_user_timezone", lambda user_id: "America/Los_Angeles")
    monkeypatch.setattr(reports_mod, "get_user_date_format", lambda request, date_only=False: "%d/%m/%Y")

    # The bucket start is already user-local; converting it as if it were a UTC
    # boundary would shift the label to the previous day for this timezone.
    def _boom(*args, **kwargs):
        raise AssertionError("convert_utc_to_user_timezone must not be called for bucket labels")

    monkeypatch.setattr(reports_mod, "convert_utc_to_user_timezone", _boom)

    payload = reports_mod.email_volume_report(
        _req(user_id=1), start_date="2026-01-15", end_date="2026-01-15"
    )

    assert payload["labels"] == ["15/01/2026"]
    assert payload["email_counts"] == [4]


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
# get_user_date_format: delegates to the shared display-prefs resolution
# ---------------------------------------------------------------------------

def test_get_user_date_format_two_queries_with_user_override(monkeypatch):
    # The query logic now lives in shared/timezone.get_display_prefs (tested
    # directly below); here we pin the delegation contract: user override
    # beats global and the time format is retained.
    monkeypatch.setattr(
        reports_mod,
        "get_display_prefs",
        lambda user_id, query=None: ("UTC", "%d %b %Y", "%H:%M"),
    )

    fmt = reports_mod.get_user_date_format(_req(user_id=7), date_only=True)
    assert fmt == "%d %b %Y"  # user override beats global

    fmt_full = reports_mod.get_user_date_format(_req(user_id=7))
    assert fmt_full == "%d %b %Y %H:%M"  # time format retained


def test_get_user_date_format_global_only_when_no_user(monkeypatch):
    monkeypatch.setattr(
        reports_mod,
        "get_display_prefs",
        lambda user_id, query=None: ("UTC", "%d/%m/%Y", "%I:%M %p"),
    )

    assert reports_mod.get_user_date_format(_req()) == "%d/%m/%Y %I:%M %p"


def test_get_display_prefs_user_beats_global_beats_default():
    """Shared resolution: user override wins, then global, then built-in."""
    calls = []

    def fake_query(sql, params=None):
        calls.append(sql)
        if "FROM users" in sql:
            return FakeResult([{
                "timezone": "Europe/London",
                "date_format": "%d %b %Y",
                "time_format": None,
            }])
        if "FROM settings" in sql:
            return FakeResult([
                {"key": "timezone", "value": "Australia/Melbourne"},
                {"key": "date_format", "value": "%Y-%m-%d"},
                {"key": "time_format", "value": "%H:%M"},
            ])
        return FakeResult([])

    # User override wins for timezone/date; missing user time falls back to global
    tz, date_fmt, time_fmt = timezone_mod.get_display_prefs(7, query=fake_query)
    assert tz == "Europe/London"
    assert date_fmt == "%d %b %Y"
    assert time_fmt == "%H:%M"
    assert len(calls) == 2  # one users query + one settings query

    # No user: global only, no users query
    tz2, date_fmt2, time_fmt2 = timezone_mod.get_display_prefs(None, query=fake_query)
    assert tz2 == "Australia/Melbourne"
    assert date_fmt2 == "%Y-%m-%d"
    assert time_fmt2 == "%H:%M"
    assert len(calls) == 3  # first call (2 queries) + one settings query

    # Query hook failures fall back to built-in defaults
    def _no_db(sql, params=None):
        raise AssertionError("no database access expected")

    tz3, date_fmt3, time_fmt3 = timezone_mod.get_display_prefs(7, query=_no_db)
    assert (tz3, date_fmt3, time_fmt3) == ("UTC", "%d %b %Y", "%H:%M")


def test_get_display_prefs_cached_within_ttl(monkeypatch):
    """Repeated resolutions for the same user+query cost one query, and
    get_user_timezone shares the same cache entry."""
    calls = []

    def fake_query(sql, params=None):
        calls.append(sql)
        if "FROM users" in sql:
            return FakeResult([{
                "timezone": "Europe/London",
                "date_format": "%d %b %Y",
                "time_format": "%H:%M",
            }])
        return FakeResult([])

    for _ in range(5):
        assert timezone_mod.get_display_prefs(7, query=fake_query) == ("Europe/London", "%d %b %Y", "%H:%M")
    # 5 pref resolutions + 5 timezone resolutions => one users query (settings
    # skipped because the user row supplied all three values)
    for _ in range(5):
        assert timezone_mod.get_user_timezone(7, query=fake_query) == "Europe/London"
    assert len(calls) == 1


def test_invalidate_display_prefs_forces_reload(monkeypatch):
    """Invalidating a user (or everything) drops the cached answer so the next
    resolution re-queries."""
    calls = []

    def fake_query(sql, params=None):
        calls.append(sql)
        if "FROM users" in sql:
            return FakeResult([{
                "timezone": "Europe/London",
                "date_format": "%d %b %Y",
                "time_format": "%H:%M",
            }])
        return FakeResult([])

    timezone_mod.get_display_prefs(7, query=fake_query)
    timezone_mod.invalidate_display_prefs(7)
    timezone_mod.get_display_prefs(7, query=fake_query)
    assert len(calls) == 2

    timezone_mod.get_display_prefs(7, query=fake_query)
    timezone_mod.invalidate_display_prefs()
    timezone_mod.get_display_prefs(7, query=fake_query)
    assert len(calls) == 3


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
    assert '<div class="chart-container storage-chart-container" id="storageTrendsChartContainer">' in template
    assert '<canvas id="storageTrendsChart"></canvas>' in template
    assert "text: 'Storage Usage Over Time'" in storage_script
    assert "maintainAspectRatio: false" in storage_script
    assert ".storage-chart-container" in styles
    assert "inline-size: 100%" in styles
    assert template.count("maintainAspectRatio: false") >= 10
    performance_start = template.index("function loadSystemPerformanceReport()")
    performance_end = template.index("// Load Security & Access Report", performance_start)
    performance_script = template[performance_start:performance_end]
    assert performance_script.count("maintainAspectRatio: false") == 2
    assert ".chart-container > canvas" in styles
    assert "width: 100% !important" in styles


# ---------------------------------------------------------------------------
# Dashboard trend widgets: per-day series for sparklines
# ---------------------------------------------------------------------------

def _series_fake_query(count_row, series_rows):
    def handler(sql, params):
        if "GROUP BY" in sql:  # the series query also contains COUNT(*)
            return FakeResult(series_rows)
        if "COUNT(*)" in sql:
            return FakeResult([count_row])
        raise AssertionError(f"Unexpected query: {sql[:100]}")
    return handler


def test_emails_last_7d_returns_zero_filled_series(monkeypatch):
    today = dashboard_mod.datetime.now(dashboard_mod.timezone.utc).date()
    monkeypatch.setattr(
        dashboard_mod,
        "query",
        fake_query_dispatch(_series_fake_query(
            {"count": 12},
            [
                {"day": today - dashboard_mod.timedelta(days=1), "c": 5},
                {"day": today, "c": 2},
            ],
        )),
    )

    data = dashboard_mod.get_emails_last_7d(_req(1))

    assert data["count"] == 12
    assert len(data["series"]) == 7
    # Oldest day first: yesterday's 5 sits at index 5, today's 2 at index 6
    assert data["series"][:5] == [0, 0, 0, 0, 0]
    assert data["series"][5] == 5
    assert data["series"][6] == 2


def test_emails_last_30d_returns_zero_filled_series(monkeypatch):
    today = dashboard_mod.datetime.now(dashboard_mod.timezone.utc).date()
    monkeypatch.setattr(
        dashboard_mod,
        "query",
        fake_query_dispatch(_series_fake_query(
            {"count": 40},
            [{"day": today - dashboard_mod.timedelta(days=29), "c": 7}],
        )),
    )

    data = dashboard_mod.get_emails_last_30d(_req(1))

    assert data["count"] == 40
    assert len(data["series"]) == 30
    assert data["series"][0] == 7
    assert sum(data["series"]) == 7


def test_emails_trend_endpoints_require_login():
    assert dashboard_mod.get_emails_last_7d(_req()).status_code == 401
    assert dashboard_mod.get_emails_last_30d(_req()).status_code == 401
