import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import logging
import gettext
import locale

from routes import (
    emails, fetch_accounts, global_settings, login, users, profile, logs,
    dashboard, worker_status, oauth, donate, help, about, reports, alerts,
    alert_management, quarantine, roles
)
from utils.logger import log
from utils.config import get_config
from utils.db import query, execute

# ---------------------------------------------------------
# Helper: Extract client IP safely (proxy-aware)
# ---------------------------------------------------------
def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------
SESSION_SECRET = get_config("SESSION_SECRET", "change-me")
if SESSION_SECRET == "change-me":
    logging.warning("⚠️  SESSION_SECRET is set to default value. Please set a secure secret in production!")

app = FastAPI(
    title="Daygle Mail Archiver",
    description="Email archiving and management system",
    version="1.0.0"
)

# ---------------------------------------------------------
# CORS Middleware
# ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# Session Middleware
# ---------------------------------------------------------
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=86400,  # 24 hours
    same_site="lax",
    https_only=False  # Set to True in production
)

# ---------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# ---------------------------------------------------------
# Setup Completion Middleware
# ---------------------------------------------------------
@app.middleware("http")
async def check_setup_completion(request: Request, call_next):
    skip_paths = ["/setup", "/login", "/health", "/static", "/403", "/about", "/help"]
    if any(request.url.path.startswith(path) for path in skip_paths):
        return await call_next(request)

    from routes.login import is_setup_complete
    if not is_setup_complete():
        return RedirectResponse("/setup", status_code=303)

    return await call_next(request)

# ---------------------------------------------------------
# NEW: Last Seen + IP Tracking Middleware
# ---------------------------------------------------------
@app.middleware("http")
async def update_last_seen(request: Request, call_next):
    response = await call_next(request)

    if "session" not in request.scope:
        return response

    user_id = request.session.get("user_id")
    if not user_id:
        return response

    try:
        # Skip auto-logout checks for authentication/setup endpoints
        skip_paths = ["/login", "/set-password", "/setup", "/logout", "/static"]
        if any(request.url.path.startswith(p) for p in skip_paths):
            try:
                ip = get_client_ip(request)
                execute(
                    """
                    UPDATE users
                    SET last_seen = NOW(),
                        last_login_ip = :ip
                    WHERE id = :uid
                    """,
                    {"uid": user_id, "ip": ip}
                )
            except Exception as e:
                logging.error(f"Failed to update last_seen on skip path: {e}")
            return response

        # Auto-logout check
        setting = query("SELECT value FROM settings WHERE key = 'auto_logout_minutes'").mappings().first()
        minutes = None
        if setting:
            try:
                minutes = int(setting["value"])
            except Exception:
                minutes = None

        # Debug: report auto-logout config and last_seen for this user
        try:
            print(f"DEBUG: auto_logout_minutes={minutes} for user_id={user_id}")
        except Exception:
            pass

        if minutes and minutes > 0:
            user = query("SELECT last_seen FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            last_seen = user.get("last_seen") if user else None
            try:
                from datetime import datetime, timedelta
                import pytz
                now = datetime.now(pytz.UTC)
                print(f"DEBUG: last_seen={last_seen} now={now}")
                if last_seen and (now - last_seen) > timedelta(minutes=minutes):
                    username = request.session.get("username", "unknown")
                    print(f"DEBUG: Auto-logout triggered for user {username} (idle > {minutes} minutes)")
                    log("info", "Security", f"Auto-logout due to inactivity for user {username}")
                    request.session.clear()
                    return RedirectResponse("/login", status_code=303)
            except Exception as e:
                print(f"DEBUG: error evaluating auto-logout: {e}")

        # Update last_seen + IP
        ip = get_client_ip(request)
        execute(
            """
            UPDATE users
            SET last_seen = NOW(),
                last_login_ip = :ip
            WHERE id = :uid
            """,
            {"uid": user_id, "ip": ip}
        )

    except Exception as e:
        logging.error(f"Failed to update last_seen or auto-logout: {e}")

    return response

# ---------------------------------------------------------
# Global Exception Handlers
# ---------------------------------------------------------
@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    log("error", "System", f"Internal server error: {str(exc)}", "")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error. Please try again later."}
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse(status_code=404, content={"error": "Endpoint not found"})
    return RedirectResponse("/login", status_code=303)

# ---------------------------------------------------------
# Static Files
# ---------------------------------------------------------
BASE_DIR = Path(__file__).parent
if (BASE_DIR / "static").exists():
    static_dir = BASE_DIR / "static"
else:
    static_dir = BASE_DIR.parent / "static"

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

fa_webfonts_dir = static_dir / "vendor" / "fontawesome" / "webfonts"
if fa_webfonts_dir.exists():
    app.mount(
        "/static/vendor/webfonts",
        StaticFiles(directory=str(fa_webfonts_dir)),
        name="fa_webfonts",
    )

# ---------------------------------------------------------
# Routers
# ---------------------------------------------------------
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

# ---------------------------------------------------------
# Root + Utility Endpoints
# ---------------------------------------------------------
@app.get("/")
def root(request: Request):
    from routes.login import is_setup_complete
    if not is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    # If the visitor is authenticated send them to the dashboard, otherwise show the login page
    if request.session.get("username"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)

@app.get("/403")
def forbidden(request: Request):
    from utils.templates import templates
    return templates.TemplateResponse("403.html", {"request": request})

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "daygle-mail-archiver",
        "version": "1.0.0"
    }

# ---------------------------------------------------------
# Startup / Shutdown Logging
# ---------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    log("info", "System", "Daygle Mail Archiver API started", "")

@app.on_event("shutdown")
async def shutdown_event():
    log("info", "System", "Daygle Mail Archiver API shutting down", "")