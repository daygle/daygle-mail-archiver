"""Worker-side alert-trigger lookups with a short TTL cache.

``create_alert(..., trigger_key=...)`` resolves the trigger's enabled flag and
configured severity from the ``alert_triggers`` table. Two worker call sites
run once per email: virus detection (worker.py) and scan errors
(clamav_scanner.py). In a burst -- a mailbomb, or a ClamAV daemon failing on
every message -- that is one ``alert_triggers`` SELECT per message even though
the row cannot change mid-run. Triggers only change when an admin edits the
alert-management page, so the lookup is cached for a short TTL and N per-email
lookups collapse to one query per interval (the same cadence the ClamAV
scanner uses to refresh its own settings).

The module is process-agnostic: callers pass their database ``query`` hook
explicitly (both worker modules share the same one). This mirrors how
``shared/timezone.py`` takes an optional ``query=`` rather than importing a
process's database layer.
"""

import time

_CACHE_TTL_SECONDS = 30.0

# trigger_key -> (expires_at, (enabled, alert_type) or None).
# A missing row is cached as None too: within the TTL the answer is stable.
_cache: dict[str, tuple[float, tuple[bool, str] | None]] = {}


def get_alert_trigger(trigger_key: str, query=None) -> tuple[bool, str] | None:
    """Return ``(enabled, alert_type)`` for a trigger key, or ``None``.

    ``None`` is returned when the trigger row does not exist or the lookup
    fails; callers then fall back to the alert type passed explicitly.
    Transient lookup failures are not cached so the next call retries.
    """
    now = time.monotonic()
    cached = _cache.get(trigger_key)
    if cached is not None and cached[0] > now:
        return cached[1]

    if query is None:
        return None

    try:
        result = query(
            "SELECT alert_type, enabled FROM alert_triggers WHERE trigger_key = :key",
            {"key": trigger_key},
        ).mappings().first()
    except Exception:
        # Do not cache transient failures; the next call retries.
        return None

    value = (bool(result["enabled"]), result["alert_type"]) if result else None
    _cache[trigger_key] = (now + _CACHE_TTL_SECONDS, value)
    return value