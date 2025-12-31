from fastapi import FastAPI
from sqlalchemy import create_engine, text
import os

app = FastAPI()

engine = create_engine(os.getenv("DB_DSN"), future=True)

@app.get("/messages")
def list_messages():
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT * FROM messages ORDER BY id DESC LIMIT 100")).mappings().all()
        return list(rows)