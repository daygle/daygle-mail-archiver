import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader

from routes import messages, imap_accounts, settings, auth, error_log, users, profile

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me")

app = FastAPI()

# Sessions
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

# Static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Routers
app.include_router(auth.router)
app.include_router(messages.router)
app.include_router(imap_accounts.router)
app.include_router(settings.router)
app.include_router(error_log.router)
app.include_router(users.router)
app.include_router(profile.router)

@app.get("/")
def root():
    return {"status": "ok"}