import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from routes import emails, fetch_accounts, settings, auth, users, profile, logs, dashboard, worker_status, oauth

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")

app = FastAPI()

# Sessions
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Static files - handle both Docker (running from /app) and local (running from src/)
BASE_DIR = Path(__file__).parent
if (BASE_DIR / "static").exists():
    # Running from Docker (/app/app.py with /app/static)
    static_dir = BASE_DIR / "static"
else:
    # Running locally from src/ with ../static
    static_dir = BASE_DIR.parent / "static"

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(emails.router)
app.include_router(fetch_accounts.router)
app.include_router(worker_status.router)
app.include_router(oauth.router)
app.include_router(users.router)
app.include_router(profile.router)
app.include_router(logs.router)
app.include_router(settings.router)

@app.get("/")
def root():
    return {"status": "ok"}