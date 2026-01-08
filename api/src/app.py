import os
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.cors import CORSMiddleware
import logging

from routes import emails, fetch_accounts, global_settings, login, users, profile, logs, dashboard, worker_status, oauth, donate, help, reports, alerts, alert_management, quarantine
from utils.logger import log
from utils.config import get_config

# Configuration
SESSION_SECRET = get_config("SESSION_SECRET", "change-me")
if SESSION_SECRET == "change-me":
    logging.warning("⚠️  SESSION_SECRET is set to default value. Please set a secure secret in production!")

app = FastAPI(
    title="Daygle Mail Archiver",
    description="Email archiving and management system",
    version="1.0.0"
)

# CORS Middleware (configure based on your needs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure this properly for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session Middleware
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=86400,  # 24 hours
    same_site="lax",
    https_only=False  # Set to True in production with HTTPS
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

# Global Exception Handler
@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: Exception):
    log("error", "System", f"Internal server error: {str(exc)}", "")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error. Please try again later."}
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception):
    # Redirect to login for authenticated pages, otherwise return JSON
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=404,
            content={"error": "Endpoint not found"}
        )
    return RedirectResponse("/login", status_code=303)

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
app.include_router(login.router)
app.include_router(dashboard.router)
app.include_router(emails.router)
app.include_router(fetch_accounts.router)
app.include_router(worker_status.router)
app.include_router(oauth.router)
app.include_router(users.router)
app.include_router(profile.router)
app.include_router(logs.router)
app.include_router(reports.router)
app.include_router(alerts.router)
app.include_router(global_settings.router)
app.include_router(alert_management.router)
app.include_router(donate.router)
app.include_router(help.router)

@app.get("/")
def root():
    """Redirect root to dashboard"""
    return RedirectResponse("/dashboard", status_code=303)

@app.get("/health")
def health_check():
    """Health check endpoint for monitoring"""
    return {
        "status": "healthy",
        "service": "daygle-mail-archiver",
        "version": "1.0.0"
    }

@app.on_event("startup")
async def startup_event():
    """Log application startup"""
    log("info", "System", "Daygle Mail Archiver API started", "")

@app.on_event("shutdown")
async def shutdown_event():
    """Log application shutdown"""
    log("info", "System", "Daygle Mail Archiver API shutting down", "")