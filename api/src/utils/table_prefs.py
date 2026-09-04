"""Per-user table column visibility preferences.

Hidden-column sets are stored per user in ``user_table_prefs.hidden_columns``
as a JSONB object keyed by page name, e.g.
``{"emails": ["to", "folder"], "quarantine": ["account"]}``.

The table is separate from ``user_widget_settings`` deliberately: the
dashboard's save endpoint replaces the whole settings blob, so sharing a row
would let one page clobber the other's preferences.
"""

import json

from .db import query

#: Columns a user is allowed to hide on each table page.
HIDEABLE_COLUMNS = {
    "emails": ("to", "folder"),
    "quarantine": ("account", "quarantined"),
}


def _read_all(user_id):
    """Return the full hidden_columns dict for a user (or {} on any failure)."""
    try:
        row = query(
            "SELECT hidden_columns FROM user_table_prefs WHERE user_id = :user_id",
            {"user_id": user_id},
        ).mappings().first()
    except Exception:
        return {}
    if not row or not row["hidden_columns"]:
        return {}
    if isinstance(row["hidden_columns"], dict):
        return row["hidden_columns"]
    try:
        parsed = json.loads(row["hidden_columns"]) if isinstance(row["hidden_columns"], str) else {}
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def get_hidden_columns(user_id, page):
    """Return the list of hidden column keys for ``page`` (empty if none/unknown)."""
    if user_id is None or page not in HIDEABLE_COLUMNS:
        return []
    allowed = set(HIDEABLE_COLUMNS[page])
    cols = _read_all(user_id).get(page, [])
    if not isinstance(cols, list):
        return []
    return [c for c in cols if c in allowed]


def save_hidden_columns(user_id, page, hidden):
    """Persist the hidden-column list for ``page``; returns True on success.

    Read-modify-write keeps each page's key independent, so emails and
    quarantine preferences never clobber each other.
    """
    if user_id is None or page not in HIDEABLE_COLUMNS:
        return False
    allowed = set(HIDEABLE_COLUMNS[page])
    if not isinstance(hidden, list):
        return False
    cleaned = [c for c in hidden if c in allowed]

    existing = _read_all(user_id)
    existing[page] = cleaned
    try:
        query(
            """
            INSERT INTO user_table_prefs (user_id, hidden_columns, updated_at)
            VALUES (:user_id, CAST(:hidden AS jsonb), NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET hidden_columns = CAST(:hidden AS jsonb), updated_at = NOW()
            """,
            {"user_id": user_id, "hidden": json.dumps(existing)},
        )
        return True
    except Exception:
        return False