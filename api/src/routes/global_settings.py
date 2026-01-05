from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/global-settings")
def settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    rows = query("SELECT key, value FROM settings").mappings().all()
    settings = {r["key"]: r["value"] for r in rows}

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "global_settings.html",
        {"request": request, "settings": settings, "flash": msg},
    )

@router.post("/global-settings")
def save_settings(
    request: Request,
    page_size: int = Form(...),
    date_format: str = Form("%d/%m/%Y"),
    time_format: str = Form("%H:%M"),
    timezone: str = Form("Australia/Melbourne"),
    enable_purge: bool = Form(False),
    retention_value: int = Form(1),
    retention_unit: str = Form("years"),
    retention_delete_from_mail_server: bool = Form(False),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    try:
        # Batch update all settings in a single query for better performance
        settings_data = [
            ('page_size', str(page_size)),
            ('date_format', date_format),
            ('time_format', time_format),
            ('timezone', timezone),
            ('enable_purge', str(enable_purge).lower()),
            ('retention_value', str(retention_value)),
            ('retention_unit', retention_unit),
            ('retention_delete_from_mail_server', str(retention_delete_from_mail_server).lower()),
        ]
        
        for key, value in settings_data:
            execute(
                """
                INSERT INTO settings (key, value)
                VALUES (:key, :value)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                {"key": key, "value": value},
            )
        
        # Update session variables
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
    except Exception as e:
        log("error", "Settings", f"Failed to save settings: {str(e)}", "")
        flash(request, f"Failed to save settings: {str(e)}")
        return RedirectResponse("/global-settings", status_code=303)

    username = request.session.get("username", "unknown")
    log("info", "Settings", f"User '{username}' updated global settings (page_size={page_size}, date_format={date_format}, time_format={time_format}, timezone={timezone}, enable_purge={enable_purge}, retention={retention_value} {retention_unit}, delete_from_mail_server={retention_delete_from_mail_server})", "")

    flash(request, "Settings updated successfully.")
    return RedirectResponse("/global-settings", status_code=303)