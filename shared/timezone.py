"""Timezone and date-formatting helpers shared between the API and worker.

This module is deliberately pure: it imports no application code and performs no
database access of its own. Lookups for per-user/global display preferences go
through an optional ``query`` hook so each process can bind its own database
layer (see ``api/src/utils/timezone.py``). When no hook is supplied the helpers
fall back to UTC and built-in formats, so formatting logic can also run with no
database at all - the worker, future worker-side display code, and unit tests
included.

Timezones are resolved through the ``tz`` parameter first, then the user
preference hook, then the global preference, then UTC. Formats follow the same
precedence with ``date_format``/``time_format``.
"""

import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import pytz

_DEFAULT_DATE_FORMAT = "%d %b %Y"
_DEFAULT_TIME_FORMAT = "%H:%M"

# Database query hook used when a caller does not pass ``query=`` explicitly.
# Processes bind their own layer here at import time (see
# api/src/utils/timezone.py); when unset (e.g. in the worker), preference
# lookups fall back to UTC and built-in formats with no database access.
_DEFAULT_QUERY = None

# Per-user display preferences are resolved by many endpoints per page load
# (each report/API route calls get_display_prefs once per request). The answer
# for a given user barely changes, so resolved values are cached for a short
# TTL -- the same cadence the worker uses to refresh its own settings. Changes
# made through the settings routes call :func:`invalidate_display_prefs` so
# they apply immediately rather than waiting out the TTL.
_PREFS_CACHE_TTL_SECONDS = 30.0

# cache key -> (expires_at, (timezone, date_format, time_format)).
# The key includes the query hook identity so callers using different
# database layers never share answers (this also keeps tests with distinct
# fakes isolated from each other).
_prefs_cache: dict[tuple[int | None, int], tuple[float, tuple[str, str, str]]] = {}


def invalidate_display_prefs(user_id=None):
    """Drop cached display-preference answers.

    Pass a user id to invalidate just that user (e.g. after they save new
    date/time/timezone preferences); pass ``None`` to clear the whole cache
    (e.g. after a global settings change).
    """
    stale = [key for key in _prefs_cache if user_id is None or key[0] == user_id]
    for key in stale:
        _prefs_cache.pop(key, None)


def get_global_timezone(query=None) -> str:
    """Return the global timezone setting, or 'UTC' when unset or unavailable."""
    if query is None:
        query = _DEFAULT_QUERY
    if query is not None:
        try:
            setting = query("SELECT value FROM settings WHERE key = 'timezone'").mappings().first()
            if setting and setting["value"]:
                return setting["value"]
        except Exception:
            pass
    return "UTC"


def get_user_timezone(user_id, query=None) -> str:
    """
    Get the timezone preference for a specific user.

    Falls back to the global timezone setting if the user has not set a
    preference (or cannot be looked up). Delegates to :func:`get_display_prefs`
    so every resolution path shares one query and one cache.

    Args:
        user_id: The ID of the user (can be int, string, or None)
        query: Optional database query hook (defaults to no lookups -> 'UTC')

    Returns:
        Timezone string (e.g., 'UTC')
    """
    tz, _, _ = get_display_prefs(user_id, query=query)
    return tz


def get_display_prefs(user_id, query=None):
    """Return the user's effective display preferences in one or two queries.

    Returns a ``(timezone, date_format, time_format)`` tuple. A user-level value
    wins when present; otherwise the global setting is used; otherwise the
    built-in default (``'%d %b %Y'`` / ``'%H:%M'`` / ``'UTC'``) applies.

    List pages call this once per request and pass the resolved values into
    :func:`format_datetime`, avoiding repeated per-row settings lookups.

    Args:
        user_id: The ID of the user (can be int, string, or None)
        query: Optional database query hook; when omitted, the process's
               default hook (``_DEFAULT_QUERY``) is used, else built-in
               defaults with no database access.
    """
    tz = date_format = time_format = None

    if user_id is not None and not isinstance(user_id, int):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            user_id = None

    if query is None:
        query = _DEFAULT_QUERY
    if query is not None:
        # A user's effective preferences are stable within the TTL: the reports
        # page alone resolves them once per endpoint per request, so a cache hit
        # removes up to two queries per endpoint. Transient failures are never
        # cached so the next call retries.
        cache_key = (user_id, id(query))
        cached = _prefs_cache.get(cache_key)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]

        if user_id is not None:
            try:
                user = query(
                    "SELECT timezone, date_format, time_format FROM users WHERE id = :id",
                    {"id": user_id},
                ).mappings().first()
            except Exception:
                user = None
            if user:
                tz = user.get("timezone") or None
                date_format = user.get("date_format") or None
                time_format = user.get("time_format") or None

        # Global settings only fill the gaps: when the user row supplies all
        # three values this second query is skipped entirely.
        if not (tz and date_format and time_format):
            try:
                rows = query(
                    "SELECT key, value FROM settings WHERE key IN ('timezone', 'date_format', 'time_format')"
                ).mappings().all()
            except Exception:
                rows = []
            settings = {row["key"]: row["value"] for row in rows if row.get("key")}

            if not tz:
                tz = settings.get("timezone")
            if not date_format:
                date_format = settings.get("date_format")
            if not time_format:
                time_format = settings.get("time_format")

    if not tz:
        tz = "UTC"
    if not date_format:
        date_format = _DEFAULT_DATE_FORMAT
    if not time_format:
        time_format = _DEFAULT_TIME_FORMAT

    # Cache the fully-resolved tuple (with defaults applied) so a later hit
    # never returns None components.
    if query is not None:
        _prefs_cache[cache_key] = (time.monotonic() + _PREFS_CACHE_TTL_SECONDS, (tz, date_format, time_format))
    return tz, date_format, time_format


def convert_utc_to_timezone(utc_datetime, target_timezone: str):
    """
    Convert a UTC datetime to a specific timezone.

    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive) or date object
        target_timezone: Timezone string (e.g., 'Australia/Melbourne')

    Returns:
        Datetime object converted to target timezone, or None for None input.
        Falls back to the input unchanged when the timezone is invalid.
    """
    if utc_datetime is None:
        return None

    # Treat naive datetimes as UTC; date objects as midnight UTC.
    if hasattr(utc_datetime, "tzinfo"):
        if utc_datetime.tzinfo is None:
            utc_datetime = pytz.utc.localize(utc_datetime)
    else:
        # It's a date object
        utc_datetime = datetime.combine(utc_datetime, datetime.min.time())
        utc_datetime = pytz.utc.localize(utc_datetime)

    # Convert to target timezone
    try:
        tz = pytz.timezone(target_timezone)
        return utc_datetime.astimezone(tz)
    except Exception:
        # If invalid timezone, fall back to UTC
        return utc_datetime


def convert_utc_to_user_timezone(utc_datetime, user_id, tz: str = None, query=None):
    """
    Convert a UTC datetime to the user's preferred timezone.

    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive)
        user_id: The ID of the user (can be int, string, or None)
        tz: An optional pre-resolved timezone name. When omitted, the user's
            timezone is looked up through ``query`` (falling back to global
            setting, then UTC).
        query: Optional database query hook used only when ``tz`` is omitted.

    Returns:
        Datetime object converted to user's timezone (or UTC if unavailable)
    """
    if utc_datetime is None:
        return None

    if tz is None:
        tz = get_user_timezone(user_id, query=query)
    return convert_utc_to_timezone(utc_datetime, tz)


def format_datetime(utc_datetime, user_id, date_format: str = None, time_format: str = None,
                    tz: str = None, query=None):
    """
    Convert a UTC datetime to the user's timezone and format it per preference.

    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive)
        user_id: The ID of the user (for timezone/format preference resolution)
        date_format: Optional date format string. If not provided, uses the user's
            preference (via ``query``) or the built-in default.
        time_format: Optional time format string. Same resolution as date_format.
        tz: Optional pre-resolved timezone name.
        query: Optional database query hook used for missing preferences.

    Returns:
        Formatted datetime string ('' for None input)
    """
    if utc_datetime is None:
        return ""

    # Resolve any missing preference in a single pass so callers that format
    # many datetimes do not pay a per-row settings lookup.
    if tz is None or date_format is None or time_format is None:
        resolved_tz, resolved_date, resolved_time = get_display_prefs(user_id, query=query)
        if tz is None:
            tz = resolved_tz
        if date_format is None:
            date_format = resolved_date
        if time_format is None:
            time_format = resolved_time

    # Convert to the target timezone (tz is guaranteed non-None after resolution)
    local_datetime = convert_utc_to_user_timezone(utc_datetime, user_id, tz=tz)

    full_format = f"{date_format} {time_format}"
    return local_datetime.strftime(full_format)


def format_email_date(date_value, fallback_datetime, user_id, date_format: str = None,
                      time_format: str = None, tz: str = None, query=None):
    """
    Format an email's Date field, falling back to a fallback timestamp.

    Args:
        date_value: RFC822 date string or datetime object from the email's Date
                    header (may be None/empty)
        fallback_datetime: A datetime object used when date_value is absent or
                           unparseable (e.g. created_at)
        user_id: The ID of the user (for timezone/format preferences)
        date_format/time_format/tz/query: forwarded to :func:`format_datetime`

    Returns:
        Formatted datetime string, or None if no usable date is available
    """
    if date_value:
        if hasattr(date_value, "strftime"):
            # Already a datetime object
            return format_datetime(date_value, user_id, date_format=date_format,
                                   time_format=time_format, tz=tz, query=query)
        try:
            return format_datetime(parsedate_to_datetime(date_value), user_id,
                                   date_format=date_format, time_format=time_format,
                                   tz=tz, query=query)
        except (ValueError, TypeError):
            # Unparseable string - fall through to use the fallback timestamp
            pass

    # No Date header or unparseable value: use the fallback timestamp
    if fallback_datetime:
        return format_datetime(fallback_datetime, user_id, date_format=date_format,
                               time_format=time_format, tz=tz, query=query)

    return None


def _user_tz_or_utc(user_id, query=None) -> pytz.BaseTzInfo:
    """Resolve the user's timezone as a pytz object, falling back to UTC."""
    user_tz_str = get_user_timezone(user_id, query=query)
    try:
        return pytz.timezone(user_tz_str)
    except Exception:
        return pytz.utc


def user_date_to_utc_range_start(date_str: str, user_id, query=None) -> datetime:
    """
    Convert a date string (YYYY-MM-DD) representing a date in the user's timezone to
    a UTC datetime for the start of that day (midnight in the user's timezone).

    Returns:
        UTC-aware datetime object representing the start of the day in user's timezone
    """
    user_tz = _user_tz_or_utc(user_id, query=query)
    d = datetime.strptime(date_str, "%Y-%m-%d")
    local_midnight = user_tz.localize(d)
    return local_midnight.astimezone(pytz.utc)


def user_date_to_utc_range_end(date_str: str, user_id, query=None) -> datetime:
    """
    Convert a date string (YYYY-MM-DD) representing a date in the user's timezone to
    a UTC datetime for the exclusive end of that day (midnight at the start of the next
    day in the user's timezone).

    Returns:
        UTC-aware datetime object representing the start of the next day in user's timezone
    """
    user_tz = _user_tz_or_utc(user_id, query=query)
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d_next = d + timedelta(days=1)
    local_midnight_next = user_tz.localize(d_next)
    return local_midnight_next.astimezone(pytz.utc)
