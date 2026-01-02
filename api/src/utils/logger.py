from datetime import datetime, timezone
from utils.db import execute

def log(level: str, source: str, message: str, details: str = ""):
    execute(
        """
        INSERT INTO error_log (timestamp, level, source, message, details)
        VALUES (:ts, :level, :source, :message, :details)
        """,
        {
            "ts": datetime.now(timezone.utc),
            "level": level,
            "source": source,
            "message": message[:500],
            "details": details[:4000],
        },
    )