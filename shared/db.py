"""Shared database access for the API and worker processes.

The API and worker historically each carried a private copy of this module.
This is the canonical implementation: it only depends on SQLAlchemy and takes
its DSN from the caller, so each process can resolve configuration (environment
variables, the .conf file, .env-dev) exactly as it did before.

A single :class:`Database` instance is created per process and exposed through
the thin per-application wrappers (``api/src/utils/db.py`` and
``worker/src/db.py``), so existing ``from ...db import query`` call sites and
their test monkeypatching are unaffected.
"""
from contextlib import contextmanager

from sqlalchemy import create_engine, text


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


class Database:
    """SQLAlchemy-backed query facade for one application process.

    Args:
        dsn: The resolved database DSN (e.g. ``DB_DSN`` from the process
            configuration). The engine is created lazily by SQLAlchemy, so no
            connection is opened until the first statement runs.
    """

    def __init__(self, dsn: str):
        self.engine = create_engine(dsn, future=True)

    @contextmanager
    def transaction(self):
        """Run a block of statements atomically in a single DB transaction.

        Commits on success, rolls back on any exception. Statements must be
        executed with :func:`sqlalchemy.text` on the yielded connection, e.g.

            with db.transaction() as conn:
                conn.execute(text("INSERT ..."), {...})
                conn.execute(text("DELETE ..."), {...})
        """
        with self.engine.begin() as conn:
            yield conn

    def query(self, sql: str, params=None):
        """Execute a query and fully materialize results before closing the connection.

        If the statement returns rows, materialize them. Otherwise return an
        empty materialized result but preserve `rowcount` so callers can
        inspect it.
        """
        with self.engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            rowcount = result.rowcount
            if getattr(result, "returns_rows", False):
                rows = result.mappings().all()
            else:
                rows = []

        return MaterializedResult(rows, rowcount=rowcount)

    def execute(self, sql: str, params=None):
        """Execute a SQL statement without returning results."""
        with self.engine.begin() as conn:
            return conn.execute(text(sql), params or {})
