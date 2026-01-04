import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from routes import emails, fetch_accounts, settings, auth, users, profile, logs, dashboard, worker_status, oauth

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")

app = FastAPI()

# Sessions
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

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
app.include_router(worker_status.router)
app.include_router(settings.router)
app.include_router(users.router)
app.include_router(profile.router)
app.include_router(logs.router)

@app.get("/")
def root():
    return {"status": "ok"}