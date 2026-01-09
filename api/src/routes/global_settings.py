from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates
from utils.email import test_smtp_connection

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

    # Update session with current global theme setting
    current_global_theme = settings.get('default_theme', 'system')
    request.session["global_theme"] = current_global_theme

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "global-settings.html",
        {"request": request, "settings": settings, "flash": msg},
    )

@router.post("/global-settings")
def save_settings(
    request: Request,
    page_size: int = Form(...),
    date_format: str = Form("%d/%m/%Y"),
    time_format: str = Form("%H:%M"),
    timezone: str = Form("Australia/Melbourne"),
    default_theme: str = Form("system"),
    enable_purge: bool = Form(False),
    retention_value: int = Form(1),
    retention_unit: str = Form("years"),
    retention_delete_from_mail_server: bool = Form(False),
    clamav_enabled: bool = Form(False),
    clamav_host: str = Form("clamav"),
    clamav_port: int = Form(3310),
    clamav_action: str = Form("quarantine"),
    clamav_quarantine_in_db: bool = Form(True),
    clamav_quarantine_retention_days: int = Form(90),
    clamav_max_file_size: int = Form(10485760),
    clamav_quarantine_encrypt: bool = Form(False),
    smtp_enabled: bool = Form(False),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_use_tls: bool = Form(True),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form("Daygle Mail Archiver"),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)


    # Fetch existing settings before updating
    rows = query("SELECT key, value FROM settings").mappings().all()
    old_settings = {r["key"]: r["value"] for r in rows}

    try:
        # Batch update all settings in a single query for better performance
        # Sanitize default_theme
        default_theme = default_theme if default_theme in ('system', 'light', 'dark') else 'system'

        settings_data = [
            ('page_size', str(page_size)),
            ('date_format', date_format),
            ('time_format', time_format),
            ('timezone', timezone),
            ('default_theme', default_theme),
            ('enable_purge', str(enable_purge).lower()),
            ('retention_value', str(retention_value)),
            ('retention_unit', retention_unit),
            ('retention_delete_from_mail_server', str(retention_delete_from_mail_server).lower()),
            ('clamav_enabled', str(clamav_enabled).lower()),
            ('clamav_host', clamav_host),
            ('clamav_port', str(clamav_port)),
            ('clamav_action', clamav_action),
            ('clamav_quarantine_in_db', str(clamav_quarantine_in_db).lower()),
            ('clamav_quarantine_retention_days', str(clamav_quarantine_retention_days)),
            ('clamav_max_file_size', str(clamav_max_file_size)),
            ('clamav_quarantine_encrypt', str(clamav_quarantine_encrypt).lower()),
            ('smtp_enabled', str(smtp_enabled).lower()),
            ('smtp_host', smtp_host),
            ('smtp_port', str(smtp_port)),
            ('smtp_username', smtp_username),
            ('smtp_password', smtp_password),
            ('smtp_use_tls', str(smtp_use_tls).lower()),
            ('smtp_from_email', smtp_from_email),
            ('smtp_from_name', smtp_from_name),
        ]

        # Update session variables
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
        # Update immediate global theme for current session (affects admin who saved settings)
        request.session["global_theme"] = default_theme
        
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
        # Update immediate global theme for current session (affects admin who saved settings)
        request.session["global_theme"] = default_theme

        # Log change for default_theme if it changed
        try:
            if old_settings.get('default_theme') != default_theme:
                log("info", "Settings", f"User '{request.session.get('username', 'unknown')}' changed default_theme to {default_theme}", "")
        except Exception:
            pass

        # If default_theme changed, optionally inform or update behaviour (no further action required here).
    except Exception as e:
        log("error", "Settings", f"Failed to save settings: {str(e)}", "")
        flash(request, f"Failed to save settings: {str(e)}")
        return RedirectResponse("/global-settings", status_code=303)

    # Log only changed fields
    username = request.session.get("username", "unknown")
    changed_fields = []
    
    new_settings = {
        'page_size': str(page_size),
        'date_format': date_format,
        'time_format': time_format,
        'timezone': timezone,
        'enable_purge': str(enable_purge).lower(),
        'retention_value': str(retention_value),
        'retention_unit': retention_unit,
        'retention_delete_from_mail_server': str(retention_delete_from_mail_server).lower(),
        'clamav_enabled': str(clamav_enabled).lower(),
        'clamav_host': clamav_host,
        'clamav_port': str(clamav_port),
        'clamav_action': clamav_action,
        'smtp_enabled': str(smtp_enabled).lower(),
        'smtp_host': smtp_host,
        'smtp_port': str(smtp_port),
        'smtp_username': smtp_username,
        'smtp_password': smtp_password,
        'smtp_use_tls': str(smtp_use_tls).lower(),
        'smtp_from_email': smtp_from_email,
        'smtp_from_name': smtp_from_name,
    }

    flash(request, "Settings updated successfully.")
    return RedirectResponse("/global-settings", status_code=303)

@router.post("/api/test-smtp")
def test_smtp(request: Request):
    """Test SMTP connection and send a test email to the current user"""
    if not require_login(request):
        flash(request, "You must be logged in to test SMTP.")
        return RedirectResponse("/login", status_code=303)

    try:
        # Get current user's email address
        user_id = request.session.get("user_id")
        if not user_id:
            flash(request, "User session not found.")
            return RedirectResponse("/global-settings", status_code=303)
        
        user = query("SELECT email FROM users WHERE id = :id", {"id": int(user_id)}).mappings().first()
        if not user or not user.get("email"):
            flash(request, "Your account does not have an email address configured.")
            return RedirectResponse("/global-settings", status_code=303)

        success, message = test_smtp_connection(user["email"], int(user_id))
        if success:
            flash(request, f"SMTP test successful: {message}")
        else:
            flash(request, f"SMTP test failed: {message}")
    except Exception as e:
        log("error", "Settings", f"SMTP test failed: {str(e)}", "")
        flash(request, f"SMTP test failed: {str(e)}")
    
    return RedirectResponse("/global-settings", status_code=303)