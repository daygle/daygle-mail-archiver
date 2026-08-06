import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from .config import require_config

DB_DSN = require_config("DB_DSN")

engine = create_engine(DB_DSN, future=True)


@contextmanager
def transaction():
    """Run a block of statements atomically in a single DB transaction.

    Commits on success, rolls back on any exception. Statements must be
    executed with :func:`sqlalchemy.text` on the yielded connection, e.g.

        with transaction() as conn:
            conn.execute(text("INSERT ..."), {...})
            conn.execute(text("DELETE ..."), {...})
    """
    with engine.begin() as conn:
        yield conn

class MaterializedResult:
    """A small wrapper for materialized query results.

    Supports `.mappings().first()`, `.mappings().all()`, iteration and exposes
    `.rowcount` for callers that check it after DML statements.
    """
    def __init__(self, rows, rowcount=None):
        self._rows = rows
        self.rowcount = rowcount

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

    If the statement returns rows, materialize them. Otherwise return an
    empty materialized result but preserve `rowcount` so callers can inspect it.
    """
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        rowcount = result.rowcount
        if getattr(result, "returns_rows", False):
            rows = result.mappings().all()
        else:
            rows = []

    return MaterializedResult(rows, rowcount=rowcount)


def execute(sql: str, params=None):
    """Execute a SQL statement without returning results"""
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
