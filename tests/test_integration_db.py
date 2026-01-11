import os
import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, text

DB_DSN = os.getenv("DB_DSN")

@pytest.mark.integration
def test_db_connect_and_select_one():
    """Simple integration test: connect to DB (if DB_DSN provided) and run SELECT 1."""
    if not DB_DSN:
        pytest.skip("DB_DSN not set; skipping integration test")

    engine = create_engine(DB_DSN)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
