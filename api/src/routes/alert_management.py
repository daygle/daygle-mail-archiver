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

    # Get all alert triggers
    triggers = query("""
        SELECT id, trigger_key, name, description, alert_type, enabled
        FROM alert_triggers
        ORDER BY name
    """).mappings().all()

    # Get global alert type settings
    alert_settings = {}
    for alert_type in ['error', 'warning', 'info', 'success']:
        result = query("SELECT value FROM settings WHERE key = :key", {"key": f"alert_{alert_type}_enabled"}).mappings().first()
        alert_settings[alert_type] = result["value"].lower() == "true" if result else (alert_type in ['error', 'warning'])

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "alert-management.html",
        {
            "request": request,
            "triggers": triggers,
            "alert_settings": alert_settings,
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

@router.post("/alert-management/types/update")
def update_alert_types(request: Request,
                      alert_error_enabled: bool = Form(True),
                      alert_warning_enabled: bool = Form(True),
                      alert_info_enabled: bool = Form(False),
                      alert_success_enabled: bool = Form(False)):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=303)

    try:
        # Update each alert type setting
        settings_to_update = {
            'alert_error_enabled': alert_error_enabled,
            'alert_warning_enabled': alert_warning_enabled,
            'alert_info_enabled': alert_info_enabled,
            'alert_success_enabled': alert_success_enabled
        }

        for key, value in settings_to_update.items():
            execute("INSERT INTO settings (key, value) VALUES (:key, :value) ON CONFLICT (key) DO UPDATE SET value = :value",
                    {"key": key, "value": str(value).lower()})

        log("info", "Alert Management", "Global alert type settings updated", f"Error: {alert_error_enabled}, Warning: {alert_warning_enabled}, Info: {alert_info_enabled}, Success: {alert_success_enabled}")

        flash(request, "Global alert type settings updated successfully.")
        return RedirectResponse("/alert-management", status_code=303)
    except Exception as e:
        log("error", "Alert Management", f"Failed to update alert type settings: {str(e)}", "")
        flash(request, "Failed to update alert type settings.")
        return RedirectResponse("/alert-management", status_code=303)