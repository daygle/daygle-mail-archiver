import importlib.metadata
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from datetime import datetime, timezone

import pyclamd

from ..utils.db import query, engine
from sqlalchemy import text as sa_text
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.permissions import require_permission, PERMISSIONS

router = APIRouter()

# Constants for health check thresholds
DEFAULT_POLL_INTERVAL = 300  # 5 minutes in seconds
HEARTBEAT_MULTIPLIER = 3  # Consider stale if no heartbeat in 3x poll interval

_api_start_time: datetime = datetime.now(timezone.utc)
_PACKAGE_NAME = "daygle-mail-archiver"


def require_login(request: Request):
    return "user_id" in request.session


def get_api_status() -> dict:
    """Return basic API health info."""
    try:
        version = importlib.metadata.version(_PACKAGE_NAME)
    except importlib.metadata.PackageNotFoundError:
        version = None

    uptime_seconds = int((datetime.now(timezone.utc) - _api_start_time).total_seconds())

    days = uptime_seconds // 86400
    hours = (uptime_seconds % 86400) // 3600
    minutes = (uptime_seconds % 3600) // 60
    seconds = uptime_seconds % 60

    if days > 0:
        uptime = f"{days}d {hours}h {minutes}m"
    elif hours > 0:
        uptime = f"{hours}h {minutes}m"
    elif minutes > 0:
        uptime = f"{minutes}m {seconds}s"
    else:
        uptime = f"{seconds}s"

    return {"healthy": True, "version": version, "uptime": uptime}


def get_database_status() -> dict:
    """Check database connectivity and return a status dict."""
    try:
        with engine.connect() as conn:
            row = conn.execute(sa_text("SELECT version()")).fetchone()
            db_version = row[0] if row else None
        return {"connected": True, "version": db_version}
    except Exception as e:
        log("warning", "Database Status", f"Failed to connect to database: {e}", "")
        return {"connected": False, "version": None}


def get_clamav_status() -> dict:
    """Check ClamAV daemon connectivity and return a status dict."""
    try:
        settings = query(
            """
            SELECT key, value FROM settings
            WHERE key IN ('clamav_enabled', 'clamav_host', 'clamav_port')
            """
        ).mappings().all()
        settings_dict = {row["key"]: row["value"] for row in settings}
    except Exception as e:
        log("warning", "ClamAV Status", f"Failed to load ClamAV settings: {e}", "")
        settings_dict = {}

    enabled = settings_dict.get("clamav_enabled", "true").lower() == "true"
    host = settings_dict.get("clamav_host", "clamav")
    port = int(settings_dict.get("clamav_port", "3310"))

    if not enabled:
        return {"enabled": False, "connected": False, "version": None, "host": host, "port": port}

    try:
        scanner = pyclamd.ClamdNetworkSocket(host=host, port=port)
        if scanner.ping():
            try:
                version = scanner.version()
            except Exception as e:
                log("warning", "ClamAV Status", f"Failed to retrieve ClamAV version: {e}", "")
                version = None
            return {"enabled": True, "connected": True, "version": version, "host": host, "port": port}
        else:
            return {"enabled": True, "connected": False, "version": None, "host": host, "port": port}
    except Exception as e:
        log("warning", "ClamAV Status", f"Failed to connect to ClamAV at {host}:{port}: {e}", "")
        return {"enabled": True, "connected": False, "version": None, "host": host, "port": port}


@router.get("/worker-status", response_class=HTMLResponse)
def worker_status(request: Request, _=require_permission(PERMISSIONS["view_worker_status"])):
    """Display worker status for all fetch accounts"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    try:
        # Get all fetch accounts with their worker status
        accounts = query("""
            SELECT 
                id,
                name,
                account_type,
                enabled,
                last_heartbeat,
                last_success,
                last_error,
                poll_interval_seconds
            FROM fetch_accounts
            ORDER BY name
        """).mappings().all()
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Worker Status", f"Failed to fetch worker status for user '{username}': {str(e)}", "")
        flash = "Failed to load worker status. Please try again."
        return templates.TemplateResponse(
            "worker-status.html",
            {
                "request": request,
                "accounts": [],
                "system_health": "error",
                "system_health_label": "Error Loading Status",
                "api_status": get_api_status(),
                "database": get_database_status(),
                "clamav": get_clamav_status(),
                "flash": flash,
            },
        )

    now = datetime.now(timezone.utc)
    
    # Calculate status for each account
    account_statuses = []
    for acc in accounts:
        status = {
            "id": acc["id"],
            "name": acc["name"],
            "account_type": acc["account_type"],
            "enabled": acc["enabled"],
            "last_heartbeat": acc["last_heartbeat"],
            "last_success": acc["last_success"],
            "last_error": acc["last_error"],
            "poll_interval": acc["poll_interval_seconds"],
        }
        
        # Determine health status
        if not acc["enabled"]:
            status["health"] = "disabled"
            status["health_label"] = "Disabled"
        elif acc["last_heartbeat"]:
            # Ensure last_heartbeat has timezone info
            last_heartbeat = acc["last_heartbeat"]
            if last_heartbeat.tzinfo is None:
                last_heartbeat = last_heartbeat.replace(tzinfo=timezone.utc)
            
            time_since_heartbeat = (now - last_heartbeat).total_seconds()
            # Consider unhealthy if no heartbeat in 3x the poll interval
            max_interval = (acc["poll_interval_seconds"] or DEFAULT_POLL_INTERVAL) * HEARTBEAT_MULTIPLIER
            
            if acc["last_error"]:
                status["health"] = "error"
                status["health_label"] = "Error"
            elif time_since_heartbeat > max_interval:
                status["health"] = "stale"
                status["health_label"] = "Stale"
            else:
                status["health"] = "healthy"
                status["health_label"] = "Healthy"
        else:
            # Never run
            status["health"] = "pending"
            status["health_label"] = "Pending"
        
        # Calculate time since last heartbeat/success
        if acc["last_heartbeat"]:
            status["heartbeat_ago"] = format_time_ago(now, acc["last_heartbeat"])
        else:
            status["heartbeat_ago"] = "Never"
            
        if acc["last_success"]:
            status["success_ago"] = format_time_ago(now, acc["last_success"])
        else:
            status["success_ago"] = "Never"
        
        account_statuses.append(status)
    
    # Calculate overall system health
    enabled_accounts = [a for a in account_statuses if a["enabled"]]
    if not enabled_accounts:
        system_health = "no_accounts"
        system_health_label = "No Active Accounts"
    else:
        error_count = sum(1 for a in enabled_accounts if a["health"] == "error")
        stale_count = sum(1 for a in enabled_accounts if a["health"] == "stale")
        healthy_count = sum(1 for a in enabled_accounts if a["health"] == "healthy")
        
        if error_count > 0:
            system_health = "error"
            system_health_label = f"{error_count} Account(s) with Errors"
        elif stale_count > 0:
            system_health = "warning"
            system_health_label = f"{stale_count} Stale Account(s)"
        elif healthy_count > 0:
            system_health = "healthy"
            system_health_label = "All Systems Operational"
        else:
            system_health = "pending"
            system_health_label = "Pending First Run"

    flash = request.session.pop("flash", None)
    
    return templates.TemplateResponse(
        "worker-status.html",
        {
            "request": request,
            "accounts": account_statuses,
            "system_health": system_health,
            "system_health_label": system_health_label,
            "api_status": get_api_status(),
            "database": get_database_status(),
            "clamav": get_clamav_status(),
            "flash": flash,
        },
    )


def format_time_ago(now: datetime, past: datetime) -> str:
    """Format a time difference in a human-readable way"""
    if not past:
        return "Never"
    
    # Ensure both datetimes are timezone-aware
    if past.tzinfo is None:
        past = past.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    
    delta = now - past
    seconds = int(delta.total_seconds())
    
    # Handle negative deltas (clock skew or future dates)
    if seconds < 0:
        return "Just now"
    
    if seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    else:
        days = seconds // 86400
        return f"{days}d ago"
