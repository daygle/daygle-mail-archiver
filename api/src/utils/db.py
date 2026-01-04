import os
from sqlalchemy import create_engine, text

DB_DSN = os.getenv("DB_DSN")
if not DB_DSN:
    raise RuntimeError("DB_DSN is not set")

engine = create_engine(DB_DSN, future=True)

def query(sql: str, params=None):
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})

def execute(sql: str, params=None):
    """Execute a SQL statement without returning results"""
    with engine.begin() as conn:
        return conn.execute(text(sql), params or {})
