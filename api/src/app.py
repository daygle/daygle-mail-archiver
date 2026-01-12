import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from routes import emails, fetch_accounts, global_settings, login, users, profile, logs, dashboard, worker_status, oauth, donate, help, about, reports, alerts, alert_management, quarantine, roles
from utils.logger import log
from utils.config import get_config
from utils.db import query, execute, engine

# Configuration
SESSION_SECRET = get_config("SESSION_SECRET", "change-me")
if SESSION_SECRET == "change-me":
    logging.warning("⚠️  SESSION_SECRET is set to default value. Please set a secure secret in production!")

app = FastAPI(
    title="Daygle Mail Archiver",
    description="Email archiving and management system",
    version="1.0.0"
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=86400,
    same_site="lax",
    https_only=False
)

# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ---------------------------------------------------------
# ✅ FIXED: Activity Tracking Middleware (no BaseHTTPMiddleware)
# ---------------------------------------------------------
@app.middleware("http")
async def activity_tracking_middleware(request: Request, call_next):
    print(f"DEBUG: Activity middleware called for {request.url.path}")

    # Skip static, login/logout, health
    if (
        request.url.path.startswith("/static/")
        or request.url.path in ["/health", "/login", "/logout"]
        or request.url.path.startswith("/api/login")
        or request.url.path.startswith("/api/logout")
    ):
        return await call_next(request)

    # Ensure session exists
    if "session" not in request.scope:
        print("DEBUG: session missing from request.scope, skipping activity tracking")
        return await call_next(request)

    session = request.scope.get("session", {})
    user_id = session.get("user_id")
    print(f"DEBUG: Middleware for {request.url.path}, user_id: {user_id}")

    if user_id:
        try:
            with engine.begin() as conn:
                # Update last_activity
                conn.execute(
                    text("UPDATE users SET last_activity = :ts WHERE id = :id"),
                    {"ts": datetime.now(timezone.utc), "id": user_id}
                )
                print(f"DEBUG: Updated last_activity for user {user_id}")
                log("debug", "System", f"Updated last_activity for user {user_id}", "")

                # Inactivity timeout
                result = conn.execute(text("SELECT value FROM settings WHERE key = 'inactivity_timeout_minutes'"))
                timeout_result = result.fetchone()

                if timeout_result:
                    timeout_minutes = int(timeout_result[0])
                    if timeout_minutes > 0:
                        result = conn.execute(
                            text("SELECT last_activity FROM users WHERE id = :id"),
                            {"id": user_id}
                        )
                        last_activity_result = result.fetchone()

                        if last_activity_result and last_activity_result[0]:
                            last_activity = last_activity_result[0]

                            if isinstance(last_activity, str):
                                last_activity = datetime.fromisoformat(last_activity.replace('Z', '+00:00'))

                            if last_activity.tzinfo is None:
                                last_activity = last_activity.replace(tzinfo=timezone.utc)
                            else:
                                last_activity = last_activity.astimezone(timezone.utc)

                            if datetime.now(timezone.utc) - last_activity > timedelta(minutes=timeout_minutes):
                                request.session.clear()
                                log("info", "System", f"User {user_id} automatically logged out due to inactivity", "")
                                return RedirectResponse("/login?message=Session expired due to inactivity", status_code=303)

        except Exception as e:
            print(f"ERROR: Exception in activity middleware for user {user_id}: {str(e)}")
            log("error", "System", f"Error in activity tracking middleware for user {user_id}: {str(e)}", "")

    response = await call_next(request)
    return response

# ---------------------------------------------------------

# Global Exception Handler
@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    log("error", "System", f"Internal server error: {str(exc)}", "")
    return JSONResponse(status_code=500, content={"error": "Internal server error. Please try again later."})

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"error": "Endpoint not found"})
    return RedirectResponse("/login", status_code=303)

# Static files
BASE_DIR = Path(__file__).parent
if (BASE_DIR / "static").exists():
    static_dir = BASE_DIR / "static"
else:
    static_dir = BASE_DIR.parent / "static"

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

fa_webfonts_dir = static_dir / "vendor" / "fontawesome" / "webfonts"
if fa_webfonts_dir.exists():
    app.mount("/static/vendor/webfonts", StaticFiles(directory=str(fa_webfonts_dir)), name="fa_webfonts")

# Routers
app.include_router(login.router)
app.include_router(dashboard.router)
app.include_router(emails.router)
app.include_router(fetch_accounts.router)
app.include_router(worker_status.router)
app.include_router(oauth.router)
app.include_router(users.router)
app.include_router(roles.router)
app.include_router(profile.router)
app.include_router(logs.router)
app.include_router(reports.router)
app.include_router(alerts.router)
app.include_router(global_settings.router)
app.include_router(alert_management.router)
app.include_router(donate.router)
app.include_router(help.router)
app.include_router(about.router)
app.include_router(quarantine.router)

@app.get("/")
def root():
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "daygle-mail-archiver", "version": "1.0.0"}

@app.on_event("startup")
async def startup_event():
    log("info", "System", "Daygle Mail Archiver API started", "")
    try:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_activity TIMESTAMPTZ"))
            log("info", "System", "Ensured users.last_activity column exists", "")
            # Ensure we have a column to store the last login IP address
            conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login_ip INET"))
            log("info", "System", "Ensured users.last_login_ip column exists", "")
    except Exception as e:
        log("warning", "System", f"Could not ensure users.last_activity column: {str(e)}", "")

@app.on_event("shutdown")
async def shutdown_event():
    log("info", "System", "Daygle Mail Archiver API shutting down", "")
