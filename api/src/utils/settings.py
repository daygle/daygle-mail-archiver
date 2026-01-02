from typing import Optional, Dict
from datetime import datetime

from utils.db import query


def get_setting(key: str) -> Optional[str]:
    row = query(
        "SELECT value FROM system_settings WHERE key = :key",
        {"key": key},
    ).mappings().first()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    query(
        """
        INSERT INTO system_settings (key, value)
        VALUES (:key, :value)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        {"key": key, "value": value},
    )


def get_retention_config() -> Dict[str, Optional[str]]:
    return {
        "enabled": get_setting("retention_enabled") or "false",
        "value": get_setting("retention_value") or "1",
        "unit": get_setting("retention_unit") or "years",
        "last_run": get_setting("retention_last_run"),
    }


def set_retention_config(enabled: bool, value: int, unit: str) -> None:
    set_setting("retention_enabled", "true" if enabled else "false")
    set_setting("retention_value", str(value))
    set_setting("retention_unit", unit)


def set_retention_last_run(dt: datetime) -> None:
    set_setting("retention_last_run", dt.isoformat(timespec="seconds"))
