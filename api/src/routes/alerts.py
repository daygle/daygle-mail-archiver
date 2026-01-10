from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from typing import Optional

from utils.db import query
from utils.logger import log
from utils.templates import templates
from utils.alerts import create_alert, get_alerts, acknowledge_alert, get_unacknowledged_count
from utils.timezone import convert_utc_to_user_timezone
from utils.permissions import PermissionChecker
from routes.reports import get_user_date_format

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str, category: str = 'info'):
    request.session["flash"] = {"message": message, "type": category}

@router.get("/alerts")
def alerts_page(
    request: Request,
    page: int = 1,
    alert_type: Optional[str] = None,
    show_acknowledged: bool = False
):
    """Alerts page"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Validate parameters
    page = max(1, page)
    page_size = 50
    offset = (page - 1) * page_size

    # Get alerts
    acknowledged_filter = None if show_acknowledged else False
    alerts = get_alerts(
        limit=page_size,
        offset=offset,
        alert_type=alert_type,
        acknowledged=acknowledged_filter,
        include_details=True
    )

    # Get total count for pagination
    where_conditions = []
    params = {}

    if alert_type:
        where_conditions.append("alert_type = :alert_type")
        params["alert_type"] = alert_type

    if acknowledged_filter is not None:
        where_conditions.append("acknowledged = :acknowledged")
        params["acknowledged"] = acknowledged_filter

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)

    count_result = query(f"SELECT COUNT(*) as total FROM alerts {where_clause}", params).mappings().first()
    total_alerts = count_result["total"] if count_result else 0
    total_pages = (total_alerts + page_size - 1) // page_size

    # Get unacknowledged count for badge
    unacknowledged_count = get_unacknowledged_count()
    request.session["unacknowledged_alerts"] = unacknowledged_count

    flash_msg = request.session.pop("flash", None)

    # Get user's date/time format
    date_format = get_user_date_format(request)

    # Convert alert timestamps to user timezone
    user_id = request.session.get("user_id")
    for alert in alerts:
        if alert["created_at"]:
            alert["created_at"] = convert_utc_to_user_timezone(alert["created_at"], user_id)
        if alert["acknowledged_at"]:
            alert["acknowledged_at"] = convert_utc_to_user_timezone(alert["acknowledged_at"], user_id)

    return templates.TemplateResponse(
        "alerts.html",
        {
            "request": request,
            "alerts": alerts,
            "flash": flash_msg,
            "page": page,
            "total_pages": total_pages,
            "alert_type": alert_type,
            "show_acknowledged": show_acknowledged,
            "unacknowledged_count": unacknowledged_count,
            "date_format": date_format
        }
    )

@router.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert_api(request: Request, alert_id: int):
    """Acknowledge an alert"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session.get("user_id")
    if not user_id:
        flash(request, "User not found", 'error')
        return RedirectResponse("/alerts", status_code=303)

    try:
        success = acknowledge_alert(alert_id, user_id)
        if success:
            username = request.session.get("username", "unknown")
            log("info", "Alerts", f"User '{username}' acknowledged alert ID {alert_id}", "")
            flash(request, "Alert acknowledged successfully!", 'success')
            return RedirectResponse("/alerts", status_code=303)
        else:
            flash(request, "Alert not found or already acknowledged", 'info')
            return RedirectResponse("/alerts", status_code=303)
    except Exception as e:
        log("error", "Alerts", f"Failed to acknowledge alert {alert_id}: {str(e)}", "")
        flash(request, "Failed to acknowledge alert", 'error')
        return RedirectResponse("/alerts", status_code=303)

@router.get("/api/alerts/unacknowledged-count")
def get_unacknowledged_count_api(request: Request):
    """Get count of unacknowledged alerts"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        count = get_unacknowledged_count()
        return JSONResponse({"count": count})
    except Exception as e:
        log("error", "Alerts", f"Failed to get unacknowledged count: {str(e)}", "")
        return JSONResponse({"error": "Failed to get count"}, status_code=500)

@router.post("/api/alerts")
def create_alert_api(
    request: Request,
    alert_type: str = Form(...),
    title: str = Form(...),
    message: str = Form(...),
    details: Optional[str] = Form(None),
    send_email: bool = Form(True)
):
    """Create a new alert (admin only)"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("manage_alerts"):
        flash(request, "Access denied", 'error')
        return RedirectResponse("/alerts", status_code=303)

    if alert_type not in ['error', 'warning', 'info', 'success']:
        flash(request, "Invalid alert type", 'error')
        return RedirectResponse("/alerts", status_code=303)

    try:
        alert_id = create_alert(alert_type, title, message, details, send_email)
        username = request.session.get("username", "unknown")
        log("info", "Alerts", f"Admin '{username}' created {alert_type} alert: {title}", "")
        flash(request, "Test alert created successfully!", 'success')
        return RedirectResponse("/alerts", status_code=303)
    except Exception as e:
        log("error", "Alerts", f"Failed to create alert: {str(e)}", "")
        flash(request, "Failed to create test alert", 'error')
        return RedirectResponse("/alerts", status_code=303)