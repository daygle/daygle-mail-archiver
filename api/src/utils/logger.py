from datetime import datetime, timezone

from .db import query


_ALLOWED_LEVELS = frozenset({"debug", "info", "warning", "error", "success"})


def log(level: str, source: str, message: str, details: str = "") -> bool:
    """Best-effort persistence for an application log entry.

    Logging must never mask the original operation when the database is
    unavailable. Invalid levels are normalized rather than rejected because
    callers include both operational and audit-style messages.
    """
    normalized_level = str(level or "info").strip().lower()
    if normalized_level not in _ALLOWED_LEVELS:
        normalized_level = "info"

    try:
        query(
            """
            INSERT INTO logs (timestamp, level, source, message, details)
            VALUES (:ts, :level, :source, :message, :details)
            """,
            {
                "ts": datetime.now(timezone.utc),
                "level": normalized_level,
                "source": str(source or "System")[:200],
                "message": str(message or "")[:500],
                "details": str(details or "")[:4000],
            },
        )
        return True
    except Exception:
        # Do not recursively log this failure: the same database is the log
        # sink and may be the source of the outage.
        return False