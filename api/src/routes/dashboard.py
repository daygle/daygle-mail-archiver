from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from typing import List
from pydantic import BaseModel
from sqlalchemy import text

from ..utils.db import query, engine, transaction
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.timezone import convert_utc_to_user_timezone, get_user_timezone
from ..utils.permissions import PermissionChecker, require_permission, PERMISSIONS

router = APIRouter()

def require_login(request: Request):
    return "user_id" in getattr(request, 'session', {})

class WidgetPreference(BaseModel):
    widget_id: str
    x: int
    y: int
    w: int
    h: int
    visible: bool = True


class DashboardLayout(BaseModel):
    widgets: List[WidgetPreference]


def _normalize_dashboard_layout(layout: DashboardLayout) -> List[WidgetPreference]:
    """Deduplicate and clamp a submitted dashboard layout.

    The front-end always sends sane values, but the endpoint must not trust the
    client: duplicate widget ids would violate the (user_id, widget_id) unique
    constraint mid-save, and unbounded coordinates could bloat the table or
    render a broken layout.
    """
    MAX_WIDGETS = 64
    MAX_COORD = 1000

    seen = set()
    normalized = []
    for widget in layout.widgets:
        if not widget.widget_id or not widget.widget_id.strip():
            continue
        if widget.widget_id in seen:
            continue
        seen.add(widget.widget_id)
        normalized.append(
            WidgetPreference(
                widget_id=widget.widget_id[:100],
                x=max(0, min(MAX_COORD, widget.x)),
                y=max(0, min(MAX_COORD, widget.y)),
                w=max(1, min(MAX_COORD, widget.w)),
                h=max(1, min(MAX_COORD, widget.h)),
                visible=widget.visible,
            )
        )
        if len(normalized) >= MAX_WIDGETS:
            break
    return normalized


def flash(request: Request, message: str, category: str = 'info'):
    session = getattr(request, 'session', {})
    session["flash"] = {"message": message, "type": category}

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, _=require_permission(PERMISSIONS["view_dashboard"])):
    """Dashboard page with charts"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Set unacknowledged alerts count for bell icon
    try:
        from ..utils.alerts import get_unacknowledged_count
        unacknowledged_count = get_unacknowledged_count()
        session = getattr(request, 'session', {})
        session["unacknowledged_alerts"] = unacknowledged_count
    except Exception:
        session = getattr(request, 'session', {})
        session["unacknowledged_alerts"] = 0

    session = getattr(request, 'session', {})
    flash = session.pop("flash", None)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "flash": flash}
    )


@router.get("/api/dashboard/stats")
def dashboard_stats(request: Request):
    """Get all dashboard statistics in one call"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Total emails
        total_results = query("SELECT COUNT(*) as count FROM emails").mappings().first()
        total_emails_count = total_results["count"] if total_results else 0

        # Database size
        size_results = query("""
            SELECT 
                pg_size_pretty(pg_database_size(current_database())) as size,
                pg_database_size(current_database()) as size_bytes
        """).mappings().first()
        db_size = size_results["size"] if size_results else "0 bytes"

        # Total accounts
        accounts_results = query("SELECT COUNT(*) as count FROM fetch_accounts").mappings().first()
        total_accounts = accounts_results["count"] if accounts_results else 0

        # Emails today
        today_results = query("""
            SELECT COUNT(*) as count 
            FROM emails 
            WHERE DATE(created_at) = CURRENT_DATE
        """).mappings().first()
        emails_today = today_results["count"] if today_results else 0

        return {
            "total_emails": total_emails_count,
            "database_size": db_size,
            "total_accounts": total_accounts,
            "emails_today": emails_today
        }
    except Exception as e:
        username = getattr(request, 'session', {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch dashboard stats for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/preferences")
def get_dashboard_preferences(request: Request):
    """Get user's dashboard widget preferences"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = getattr(request, 'session', {}).get("user_id")
    
    try:
        results = query("""
            SELECT widget_id, x_position, y_position, width, height, is_visible
            FROM dashboard_preferences
            WHERE user_id = :user_id
        """, {"user_id": user_id}).mappings().all()

        widgets = [
            {
                "widget_id": row["widget_id"],
                "x": row["x_position"],
                "y": row["y_position"],
                "w": row["width"],
                "h": row["height"],
                "visible": row["is_visible"]
            }
            for row in results
        ]

        return {"widgets": widgets}
    except Exception as e:
        username = getattr(request, 'session', {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch dashboard preferences for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load preferences"}, status_code=500)


@router.post("/api/dashboard/preferences")
async def save_dashboard_preferences(request: Request, widgets: str = Form(...)):
    """Save user's dashboard widget preferences"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = getattr(request, 'session', {}).get("user_id")

    try:
        import json
        widgets_data = json.loads(widgets)

        # Validate with Pydantic (rejects malformed payloads)
        layout = DashboardLayout(widgets=widgets_data)
        widgets_list = _normalize_dashboard_layout(layout)

        # Replace the whole layout atomically: a mid-save failure must not leave
        # the user with a half-updated (or empty) set of preferences.
        with transaction() as conn:
            conn.execute(
                text("DELETE FROM dashboard_preferences WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            for widget in widgets_list:
                conn.execute(
                    text("""
                        INSERT INTO dashboard_preferences
                        (user_id, widget_id, x_position, y_position, width, height, is_visible)
                        VALUES (:user_id, :widget_id, :x, :y, :w, :h, :visible)
                    """),
                    {
                        "user_id": user_id,
                        "widget_id": widget.widget_id,
                        "x": widget.x,
                        "y": widget.y,
                        "w": widget.w,
                        "h": widget.h,
                        "visible": widget.visible
                    },
                )

        username = getattr(request, "session", {}).get("username", "unknown")
        log("info", "Dashboard", f"User '{username}' saved dashboard preferences", "")
        flash(request, "Dashboard layout saved successfully!", 'success')
        return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to save dashboard preferences for user '{username}': {str(e)}", "")
        flash(request, "Failed to save dashboard layout", 'error')
        return RedirectResponse("/dashboard", status_code=303)


@router.get("/api/dashboard/system-status")
def system_status(request: Request):
    """Get system status information"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Get database connection status
        db_status = "healthy"
        try:
            query("SELECT 1")
        except Exception:
            db_status = "error"

        # Get worker status from last heartbeat in fetch_accounts
        worker_results = query("""
            SELECT COUNT(*) as count
            FROM fetch_accounts 
            WHERE enabled = TRUE 
            AND last_heartbeat >= NOW() - INTERVAL '5 minutes'
        """).mappings().first()

        workers_online = worker_results["count"] if worker_results else 0
        
        # Get pending fetch jobs (enabled accounts)
        pending_results = query("""
            SELECT COUNT(*) as count 
            FROM fetch_accounts 
            WHERE enabled = TRUE
        """).mappings().first()
        pending_jobs = pending_results["count"] if pending_results else 0

        return {
            "database": db_status,
            "workers_online": workers_online,
            "pending_jobs": pending_jobs
        }
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch system status for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/check-updates")
def check_updates(request: Request, force: bool = False):
    """Update checks have been removed from the web UI and are no longer supported by this endpoint."""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Only administrators could previously access this; keep same restriction
    checker = PermissionChecker(request)
    if not checker.has_permission("manage_global_settings"):
        return JSONResponse({"error": "Forbidden - Administrator access required"}, status_code=403)

    # Notify caller that update checks are no longer available via the web UI
    return JSONResponse({"error": "Update checks have been removed from the web UI"}, status_code=410)

@router.get("/system-updates")
def system_updates(request: Request):
    """System updates page removed - redirect to Global Settings"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    checker = PermissionChecker(request)
    if not checker.has_permission("manage_global_settings"):
        return RedirectResponse("/dashboard", status_code=303)

    # Redirect to Global Settings as the System Updates page has been removed
    return RedirectResponse("/global-settings", status_code=303)


@router.get("/api/dashboard/clamav-stats")
def clamav_stats(request: Request):
    """Get ClamAV virus scanning statistics"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    try:
        # Get counts of emails by virus scan status
        # Quarantined: count quarantined_emails entries
        quarantined_results = query("""
            SELECT CASE WHEN to_regclass('public.quarantined_emails') IS NOT NULL
                 THEN (SELECT COUNT(*) FROM quarantined_emails)
                 ELSE 0 END as count
        """).mappings().first()
        quarantined = quarantined_results["count"] if quarantined_results else 0

        # Rejected: emails refused by the worker when clamav_action='reject'.
        # They are never stored in the emails table, so count the worker's
        # reject log entries. The worker logs these with the ACCOUNT NAME as
        # the log source (message: "Email rejected due to virus: ..."), so
        # there is no reliable source filter to apply.
        rejected_results = query("""
            SELECT COUNT(*) as count 
            FROM logs 
            WHERE message LIKE '%rejected due to virus%'
            AND timestamp >= NOW() - INTERVAL '30 days'
        """).mappings().first()
        rejected = rejected_results["count"] if rejected_results else 0

        # Logged: For now, this shows the same as quarantined since we don't have
        # a separate tracking mechanism. In future, this could track emails where
        # clamav_action='log_only' was the configured action at scan time.
        logged = quarantined
        
        # Clean: virus_scanned = TRUE and virus_detected = FALSE
        clean_results = query("""
            SELECT COUNT(*) as count 
            FROM emails 
            WHERE virus_scanned = TRUE 
            AND virus_detected = FALSE
        """).mappings().first()
        clean = clean_results["count"] if clean_results else 0
        
        return {
            "quarantined": quarantined,
            "rejected": rejected,
            "logged": logged,
            "clean": clean
        }
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch ClamAV stats for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/emails-last-7d")
def get_emails_last_7d(request: Request):
    """Get count of emails from the last 7 days"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        result = query("""
            SELECT COUNT(*) as count
            FROM emails
            WHERE created_at >= NOW() - INTERVAL '7 days'
        """).mappings().first()

        count = result["count"] if result else 0
        return {"count": count}
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch emails last 7d for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/emails-last-30d")
def get_emails_last_30d(request: Request):
    """Get count of emails from the last 30 days"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        result = query("""
            SELECT COUNT(*) as count
            FROM emails
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """).mappings().first()

        count = result["count"] if result else 0
        return {"count": count}
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch emails last 30d for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/storage-used")
def get_storage_used(request: Request):
    """Get total storage used by emails"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Execute query and immediately consume the result within transaction
        with engine.begin() as conn:
            result_proxy = conn.execute(text("""
                SELECT
                    ROUND(SUM(octet_length(raw_email)) / 1024.0 / 1024.0, 2) as size_mb
                FROM emails
            """))
            result = result_proxy.mappings().first()

        size_mb = result["size_mb"] or 0
        if size_mb >= 1024:
            size_gb = round(size_mb / 1024, 2)
            size = f"{size_gb} GB"
        else:
            size = f"{size_mb} MB"

        return {"size": size}
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch storage used for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/system-uptime")
def get_system_uptime(request: Request):
    """Get system uptime"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        import subprocess

        # Try to get Docker container uptime first
        uptime_str = "Unknown"
        try:
            # Method 1: Get container start time from stat command
            result = subprocess.run(['stat', '-c', '%Y', '/proc/1'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                start_time = int(result.stdout.strip())
                current_time = int(subprocess.run(['date', '+%s'], capture_output=True, text=True, timeout=5).stdout.strip())
                seconds = current_time - start_time
                
                # Format uptime
                days = seconds // 86400
                hours = (seconds % 86400) // 3600
                minutes = (seconds % 3600) // 60

                uptime_parts = []
                if days > 0:
                    uptime_parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours > 0:
                    uptime_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes > 0 and days == 0:
                    uptime_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

                uptime_str = ", ".join(uptime_parts) if uptime_parts else "< 1 minute"
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            try:
                # Method 2: Fallback to ps command
                result = subprocess.run(['ps', '-o', 'etimes', '-p', '1', '--no-headers'], capture_output=True, text=True, timeout=5)
                if result.returncode == 0:
                    etimes = result.stdout.strip()
                    if etimes.isdigit():
                        seconds = int(etimes)
                        # Format uptime
                        days = seconds // 86400
                        hours = (seconds % 86400) // 3600
                        minutes = (seconds % 3600) // 60

                        uptime_parts = []
                        if days > 0:
                            uptime_parts.append(f"{days} day{'s' if days != 1 else ''}")
                        if hours > 0:
                            uptime_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                        if minutes > 0 and days == 0:
                            uptime_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

                        uptime_str = ", ".join(uptime_parts) if uptime_parts else "< 1 minute"
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
                pass

        # If container uptime failed, try system uptime command
        if uptime_str == "Unknown":
            try:
                result = subprocess.run(['uptime', '-p'], capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    uptime_str = result.stdout.strip()
                    # Remove "up " prefix if present
                    if uptime_str.startswith('up '):
                        uptime_str = uptime_str[3:]
                else:
                    uptime_str = "Unknown"
            except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.SubprocessError):
                uptime_str = "System uptime information unavailable"

        return {"uptime": uptime_str}
    except Exception as e:
        try:
            username = getattr(request, 'session', {}).get("username", "unknown")
            log("error", "Dashboard", f"Failed to fetch system uptime for user '{username}': {str(e)}", "")
        except Exception:
            pass  # Don't fail if logging fails
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/widget-settings")
def get_widget_settings(request: Request):
    """Get user's widget settings (e.g., days range for charts)"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user_id = getattr(request, 'session', {}).get("user_id")
    
    try:
        result = query("""
            SELECT settings
            FROM user_widget_settings
            WHERE user_id = :user_id
        """, {"user_id": user_id}).mappings().first()

        if result and result["settings"]:
            return {"settings": result["settings"]}
        else:
            # Return default settings
            return {
                "settings": {
                    "emails-per-day": {"days": 30},
                    "deletion-stats": {"days": 30},
                    "storage-trends": {"days": 7}
                }
            }
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch widget settings for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.post("/api/dashboard/widget-settings")
async def save_widget_settings(request: Request):
    """Save user's widget settings"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = getattr(request, 'session', {}).get("user_id")
    
    try:
        data = await request.json()
        settings = data.get("settings", {})
        
        # Use PostgreSQL's JSON type to store settings
        import json
        settings_json = json.dumps(settings)
        
        # Upsert the settings
        query("""
            INSERT INTO user_widget_settings (user_id, settings, updated_at)
            VALUES (:user_id, CAST(:settings AS jsonb), NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET settings = CAST(:settings AS jsonb), updated_at = NOW()
        """, {"user_id": user_id, "settings": settings_json})
        
        username = getattr(request, "session", {}).get("username", "unknown")
        log("info", "Dashboard", f"User '{username}' saved widget settings", "")
        return JSONResponse({"message": "Widget settings saved successfully!"})
    except Exception as e:
        username = getattr(request, "session", {}).get("username", "unknown")
        log("error", "Dashboard", f"Failed to save widget settings for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to save widget settings"}, status_code=500)

