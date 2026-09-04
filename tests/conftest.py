"""Shared pytest fixtures for the whole suite."""

import pytest


@pytest.fixture(autouse=True)
def _clear_shared_caches():
    """Isolate process-wide TTL caches between tests.

    ``shared/timezone.py`` caches per-user display preferences and
    ``shared/alert_triggers.py`` caches alert-trigger lookups for a short TTL.
    Both are module-level by design (one worker/API process), so tests that
    drive the same user id or trigger key with different fake databases must
    not leak one test's answer into the next.
    """
    from shared import alert_triggers, timezone

    timezone._prefs_cache.clear()
    alert_triggers._cache.clear()
    yield
    timezone._prefs_cache.clear()
    alert_triggers._cache.clear()