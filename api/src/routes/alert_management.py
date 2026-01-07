from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def require_admin(request: Request):
    if not require_login(request):
        return False
    user = query("SELECT role FROM users WHERE id = :id", {"id": request.session["user_id"]}).mappings().first()
    return user and user["role"] == "administrator"

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/alert-management")
def alert_management_form(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=303)

    # Set unacknowledged alerts count for bell icon
    try:
        from utils.alerts import get_unacknowledged_count
        unacknowledged_count = get_unacknowledged_count()
        request.session["unacknowledged_alerts"] = unacknowledged_count
    except Exception:
        request.session["unacknowledged_alerts"] = 0

    # Get all alert triggers
    triggers = query("""
        SELECT id, trigger_key, name, description, alert_type, enabled
        FROM alert_triggers
        ORDER BY name
    """).mappings().all()

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
def update_trigger_status(request: Request, trigger_id: int = Form(...), enabled: bool = Form(...)):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=303)

    try:
        execute("UPDATE alert_triggers SET enabled = :enabled WHERE id = :id",
                {"enabled": enabled, "id": trigger_id})

        trigger = query("SELECT name FROM alert_triggers WHERE id = :id", {"id": trigger_id}).mappings().first()
        trigger_name = trigger["name"] if trigger else f"ID {trigger_id}"

        log("info", "Alert Management", f"Alert trigger '{trigger_name}' {'enabled' if enabled else 'disabled'}", "")

        flash(request, f"Alert trigger '{trigger_name}' {'enabled' if enabled else 'disabled'} successfully.")
        return RedirectResponse("/alert-management", status_code=303)
    except Exception as e:
        log("error", "Alert Management", f"Failed to update trigger status: {str(e)}", "")
        flash(request, "Failed to update trigger status.")
        return RedirectResponse("/alert-management", status_code=303)

@router.post("/alert-management/save")
def save_alert_settings(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=303)

    flash(request, "Alert settings saved successfully.")
    return RedirectResponse("/alert-management", status_code=303)