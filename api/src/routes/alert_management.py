from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from ..utils.db import query, execute
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.permissions import require_permission, PERMISSIONS

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str, category: str = 'info'):
    request.session["flash"] = {"message": message, "type": category}


def _safe_log(level: str, source: str, message: str, details: str = ""):
    """Keep error handling usable when the logging database is unavailable."""
    try:
        log(level, source, message, details)
    except Exception:
        pass

@router.get("/alert-management")
def alert_management_form(request: Request, _=require_permission(PERMISSIONS["manage_alerts"])):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Set unacknowledged alerts count for bell icon
    try:
        from ..utils.alerts import get_unacknowledged_count
        unacknowledged_count = get_unacknowledged_count()
        request.session["unacknowledged_alerts"] = unacknowledged_count
    except Exception:
        request.session["unacknowledged_alerts"] = 0

    # Get all alert triggers. A database outage should return the page with a
    # useful message rather than turn the management screen into a 500.
    try:
        triggers = query("""
            SELECT id, trigger_key, name, description, alert_type, enabled
            FROM alert_triggers
            ORDER BY name
        """).mappings().all()
    except Exception as e:
        _safe_log("error", "Alert Management", f"Failed to load alert triggers: {str(e)}", "")
        flash(request, "Unable to load alert triggers. Please try again.", "error")
        triggers = []

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "alert-management.html",
        {
            "request": request,
            "triggers": triggers,
            "flash": msg
        },
    )

@router.post("/alert-management/triggers/update")
def update_trigger_status(request: Request, _=require_permission(PERMISSIONS["manage_alerts"]), trigger_id: int = Form(...), enabled: bool = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    try:
        # Resolve the trigger before updating it so a later lookup failure cannot
        # report an update as failed after the database has already committed it.
        trigger = query(
            "SELECT name FROM alert_triggers WHERE id = :id", {"id": trigger_id}
        ).mappings().first()
        if not trigger:
            flash(request, "Alert trigger not found.", "error")
            return RedirectResponse("/alert-management", status_code=303)

        result = execute(
            "UPDATE alert_triggers SET enabled = :enabled WHERE id = :id",
            {"enabled": enabled, "id": trigger_id},
        )
        if result.rowcount == 0:
            flash(request, "Alert trigger no longer exists.", "error")
            return RedirectResponse("/alert-management", status_code=303)
        trigger_name = trigger["name"]

        _safe_log("info", "Alert Management", f"Alert trigger '{trigger_name}' {'enabled' if enabled else 'disabled'}", "")

        flash(request, f"Alert trigger '{trigger_name}' {'enabled' if enabled else 'disabled'} successfully.", 'success')
        return RedirectResponse("/alert-management", status_code=303)
    except Exception as e:
        _safe_log("error", "Alert Management", f"Failed to update trigger status: {str(e)}", "")
        flash(request, "Failed to update trigger status.", 'error')
        return RedirectResponse("/alert-management", status_code=303)

@router.post("/alert-management/triggers/update-severity")
def update_trigger_severity(request: Request, _=require_permission(PERMISSIONS["manage_alerts"]), trigger_id: int = Form(...), alert_type: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Validate alert_type
    valid_types = ['error', 'warning', 'info', 'success']
    if alert_type not in valid_types:
        flash(request, f"Invalid alert type: {alert_type}", 'error')
        return RedirectResponse("/alert-management", status_code=303)

    try:
        # Resolve the trigger before updating it so a later lookup failure cannot
        # report an update as failed after the database has already committed it.
        trigger = query(
            "SELECT name FROM alert_triggers WHERE id = :id", {"id": trigger_id}
        ).mappings().first()
        if not trigger:
            flash(request, "Alert trigger not found.", "error")
            return RedirectResponse("/alert-management", status_code=303)

        result = execute(
            "UPDATE alert_triggers SET alert_type = :alert_type WHERE id = :id",
            {"alert_type": alert_type, "id": trigger_id},
        )
        if result.rowcount == 0:
            flash(request, "Alert trigger no longer exists.", "error")
            return RedirectResponse("/alert-management", status_code=303)
        trigger_name = trigger["name"]

        _safe_log("info", "Alert Management", f"Alert trigger '{trigger_name}' severity changed to {alert_type}", "")

        flash(request, f"Alert trigger '{trigger_name}' severity updated to {alert_type}.", 'success')
        return RedirectResponse("/alert-management", status_code=303)
    except Exception as e:
        _safe_log("error", "Alert Management", f"Failed to update trigger severity: {str(e)}", "")
        flash(request, "Failed to update trigger severity.", 'error')
        return RedirectResponse("/alert-management", status_code=303)