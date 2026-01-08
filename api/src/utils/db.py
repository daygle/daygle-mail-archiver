import os
from sqlalchemy import create_engine, text
from utils.config import require_config

DB_DSN = require_config("DB_DSN")

engine = create_engine(DB_DSN, future=True)

class MaterializedResult:
    """A small wrapper for materialized query results.

    This allows existing call sites which do `query(...).mappings().first()`
    or `.mappings().all()` to continue working even after the DB
    connection has been closed.
    """
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


def query(sql: str, params=None):
    """Execute a query and fully materialize results before closing the connection.

    Returns a `MaterializedResult` so callers can safely access the rows
    after the connection has been released.
    """
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        rows = result.mappings().all()

    return MaterializedResult(rows)


def execute(sql: str, params=None):
    """Execute a SQL statement without returning results"""
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
