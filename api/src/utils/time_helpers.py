from datetime import datetime
import pytz
from utils.timezone import get_user_timezone, convert_utc_to_user_timezone


def time_ago(utc_datetime, user_id):
    """Return a human-friendly relative time string (e.g., '2 minutes ago') respecting user's timezone."""
    if not utc_datetime:
        return ""

    # Ensure we have a datetime object
    if isinstance(utc_datetime, str):
        try:
            utc_datetime = datetime.fromisoformat(utc_datetime)
        except Exception:
            return ""

    try:
        # Convert both to user's timezone
        user_tz = pytz.timezone(get_user_timezone(user_id))
        local_dt = convert_utc_to_user_timezone(utc_datetime, user_id)
        now = datetime.now(user_tz)
        diff = now - local_dt
        seconds = int(diff.total_seconds())

        if seconds < 0:
            return "just now"
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            m = seconds // 60
            return f"{m} minute{'s' if m != 1 else ''} ago"
        if seconds < 86400:
            h = seconds // 3600
            return f"{h} hour{'s' if h != 1 else ''} ago"
        if seconds < 2592000:
            d = seconds // 86400
            return f"{d} day{'s' if d != 1 else ''} ago"
        # Fallback to date formatted in user's timezone
        return local_dt.strftime('%Y-%m-%d')
    except Exception:
        return ""