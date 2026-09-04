"""Timezone/date-formatting helpers for the API process.

The canonical implementation lives in the repository-root ``shared`` package
(``shared/timezone.py``); this module simply points the shared module's default
query hook at the API's database layer and re-exports every helper under the
familiar name, so existing import sites keep working unchanged.

Preference lookups therefore run against the API database by default; callers
that want a different hook (or none) can pass ``query=`` explicitly, and
processes that never import this module (e.g. the worker) simply get UTC and
built-in formats.
"""
import sys
from pathlib import Path

from .db import query


# Make the repository-root ``shared`` package importable whether this module is
# running from the local checkout (api/src/utils/) or a container mount.
def _find_shared_root() -> Path:
    current = Path(__file__).resolve().parent
    while True:
        if (current / "shared").is_dir():
            return current
        if current.parent == current:
            raise ImportError("Cannot locate the shared/ package from " + str(__file__))
        current = current.parent


_shared_root = _find_shared_root()
if str(_shared_root) not in sys.path:
    sys.path.insert(0, str(_shared_root))

from shared import timezone as _timezone  # noqa: E402

# Bind this process's database hook: every helper that resolves preferences uses
# this when the caller does not pass an explicit ``query=``.
_timezone._DEFAULT_QUERY = query

get_global_timezone = _timezone.get_global_timezone
get_user_timezone = _timezone.get_user_timezone
get_display_prefs = _timezone.get_display_prefs
invalidate_display_prefs = _timezone.invalidate_display_prefs
convert_utc_to_timezone = _timezone.convert_utc_to_timezone
convert_utc_to_user_timezone = _timezone.convert_utc_to_user_timezone
format_datetime = _timezone.format_datetime
format_email_date = _timezone.format_email_date
user_date_to_utc_range_start = _timezone.user_date_to_utc_range_start
user_date_to_utc_range_end = _timezone.user_date_to_utc_range_end

__all__ = [
    "get_global_timezone",
    "get_user_timezone",
    "get_display_prefs",
    "invalidate_display_prefs",
    "convert_utc_to_timezone",
    "convert_utc_to_user_timezone",
    "format_datetime",
    "format_email_date",
    "user_date_to_utc_range_start",
    "user_date_to_utc_range_end",
]
