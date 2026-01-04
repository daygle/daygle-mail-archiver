from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timezone

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


@router.get("/worker-status", response_class=HTMLResponse)
def worker_status(request: Request):
    """Display worker status for all fetch accounts"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

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
            time_since_heartbeat = (now - acc["last_heartbeat"]).total_seconds()
            # Consider unhealthy if no heartbeat in 3x the poll interval (default to 5 minutes)
            max_interval = (acc["poll_interval_seconds"] or 300) * 3
            
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
        "worker_status.html",
        {
            "request": request,
            "accounts": account_statuses,
            "system_health": system_health,
            "system_health_label": system_health_label,
            "flash": flash,
        },
    )


def format_time_ago(now: datetime, past: datetime) -> str:
    """Format a time difference in a human-readable way"""
    if past.tzinfo is None:
        past = past.replace(tzinfo=timezone.utc)
    
    delta = now - past
    seconds = int(delta.total_seconds())
    
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
