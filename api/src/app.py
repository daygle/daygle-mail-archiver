import asyncio
import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import logging

from .routes import (
    emails, fetch_accounts, global_settings, login, users, profile, logs,
    dashboard, worker_status, oauth, donate, help, about, reports, alerts,
    alert_management, quarantine, roles
)
from .utils.logger import log
from .utils.config import get_config
from .utils.db import query, execute

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
    logging.error("⚠️  SESSION_SECRET is set to default value. Please set a secure secret in production!")

app = FastAPI(
    title="Daygle Mail Archiver",
    description="Email archiving and management system",
    version="1.0.0"
)

# ---------------------------------------------------------
# CORS Middleware
# Origins are configurable via the ALLOWED_ORIGINS setting (env var or the
# [security] allowed_origins conf key) as a comma-separated list. When it is
# unset or "*", we fall back to the permissive default but keep
# allow_credentials disabled, since "*" + credentials is rejected by browsers
# and unsafe. When explicit origins are configured, credentials are enabled so
# authenticated cross-origin requests from those trusted origins work.
# ---------------------------------------------------------
_allowed_origins_raw = (get_config("ALLOWED_ORIGINS", "*") or "*").strip()
if _allowed_origins_raw in ("", "*"):
    cors_allow_origins = ["*"]
    cors_allow_credentials = False
else:
    cors_allow_origins = [o.strip() for o in _allowed_origins_raw.split(",") if o.strip()]
    cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins,
    allow_credentials=cors_allow_credentials,
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
    https_only=False
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

    from .routes.login import is_setup_complete
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
            return response

        # Auto-logout check
        setting = query("SELECT value FROM settings WHERE key = 'auto_logout_minutes'").mappings().first()
        minutes = None
        if setting:
            try:
                minutes = int(setting["value"])
            except Exception:
                minutes = None

        if minutes and minutes > 0:
            user = query("SELECT last_seen FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            last_seen = user.get("last_seen") if user else None
            try:
                from datetime import datetime, timedelta
                import pytz
                now = datetime.now(pytz.UTC)
                if last_seen and (now - last_seen) > timedelta(minutes=minutes):
                    username = request.session.get("username", "unknown")
                    log("info", "Security", f"Auto-logout due to inactivity for user {username}")
                    request.session.clear()
                    return RedirectResponse("/login", status_code=303)
            except Exception as e:
                logging.debug(f"Error evaluating auto-logout: {e}")

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
# Prefer absolute (resolved) paths so StaticFiles always receives an absolute directory
if (BASE_DIR / "static").exists():
    static_dir = (BASE_DIR / "static").resolve()
else:
    static_dir = (BASE_DIR.parent / "static").resolve()

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
    from .routes.login import is_setup_complete
    if not is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    # If the visitor is authenticated send them to the dashboard, otherwise show the login page
    if request.session.get("username"):
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login", status_code=303)

@app.get("/403")
def forbidden(request: Request):
    from .utils.templates import templates
    return templates.TemplateResponse("403.html", {"request": request})

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "daygle-mail-archiver",
        "version": "1.0.0"
    }

# ---------------------------------------------------------
# Backfill date_parsed for rows where it is NULL but the raw date header is present.
# This is a one-time migration for deployments that existed before the date_parsed
# column was introduced. Without this, date-range filtering falls back to created_at
# (the archival timestamp) instead of the email's actual date, causing the filter
# to return zero results when the user selects a date range.
# ---------------------------------------------------------
def _backfill_date_parsed():
    """Populate date_parsed for emails/quarantined_emails where it is still NULL."""
    from email.utils import parsedate_to_datetime as _parsedate

    _BATCH = 500  # rows per query to keep memory usage bounded

    for table in ("emails", "quarantined_emails"):
        try:
            offset = 0
            while True:
                try:
                    rows = query(
                        f"SELECT id, date FROM {table}"
                        " WHERE date_parsed IS NULL AND date IS NOT NULL"
                        f" ORDER BY id LIMIT {_BATCH} OFFSET {offset}",
                        {},
                    ).mappings().all()
                except Exception as exc:
                    logging.warning(f"date_parsed backfill: could not query {table}: {exc}")
                    break

                if not rows:
                    break

                updates = []
                for row in rows:
                    try:
                        parsed = _parsedate(row["date"])
                        updates.append({"dp": parsed, "id": row["id"]})
                    except Exception:
                        pass  # unparseable date — leave date_parsed as NULL

                if updates:
                    for upd in updates:
                        try:
                            execute(
                                f"UPDATE {table} SET date_parsed = :dp"
                                " WHERE id = :id AND date_parsed IS NULL",
                                upd,
                            )
                        except Exception as exc:
                            logging.debug(
                                f"date_parsed backfill: could not update {table}"
                                f" id={upd['id']}: {exc}"
                            )
                    log(
                        "info", "System",
                        f"Backfilled date_parsed for {len(updates)} row(s) in {table}", "",
                    )

                offset += _BATCH
                if len(rows) < _BATCH:
                    break  # last batch — no more rows
        except Exception as exc:
            logging.warning(f"date_parsed backfill: unexpected error for {table}: {exc}")


# ---------------------------------------------------------
# Startup / Shutdown Logging
# ---------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    log("info", "System", "Daygle Mail Archiver API started", "")
    # Run the date_parsed backfill in a thread so it does not block request handling.
    # Errors are logged inside _backfill_date_parsed; we intentionally don't await the
    # task so startup completes immediately.
    asyncio.create_task(asyncio.to_thread(_backfill_date_parsed))

@app.on_event("shutdown")
async def shutdown_event():
    log("info", "System", "Daygle Mail Archiver API shutting down", "")