from sqlalchemy import create_engine, text
from config import Config

engine = create_engine(Config.DB_DSN, future=True)

def init_db():
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                account TEXT NOT NULL,
                folder TEXT NOT NULL,
                uid BIGINT NOT NULL,
                hash TEXT NOT NULL,
                subject TEXT,
                sender TEXT,
                recipients TEXT,
                date TIMESTAMP,
                storage_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'processed',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """))