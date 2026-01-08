import os
from sqlalchemy import create_engine, text
from config import require_config

DB_DSN = require_config("DB_DSN")

engine = create_engine(DB_DSN, future=True)

class MaterializedResult:
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
    with engine.begin() as conn:
        result = conn.execute(text(sql), params or {})
        rowcount = result.rowcount
        if getattr(result, "returns_rows", False):
            rows = result.mappings().all()
        else:
            rows = []

    return MaterializedResult(rows, rowcount=rowcount)


def execute(sql: str, params=None):
    with engine.begin() as conn:
        conn.execute(text(sql), params or {})