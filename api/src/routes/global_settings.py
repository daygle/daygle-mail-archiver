from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import text
import pytz
from typing import Optional

from ..utils.db import query, transaction
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.email import test_smtp_connection
from ..utils.permissions import require_permission, PERMISSIONS
from ..utils.i18n import request_gettext
from ..utils.timezone import invalidate_display_prefs

ALLOWED_THEMES = ("system", "light", "dark")
ALLOWED_RETENTION_UNITS = ("days", "months", "years")
ALLOWED_CLAMAV_ACTIONS = ("quarantine", "reject", "log_only")
_KNOWN_TIMEZONES = frozenset(pytz.all_timezones)


def validate_global_settings(values: dict, current_timezone: Optional[str] = None, _=lambda x: x) -> Optional[str]:
    """Validate submitted global-settings values.

    The HTML form constrains most fields, but the server must not trust the
    client: a destructive value such as ``retention_value=0`` would make the
    worker purge the *entire* archive on its next retention run. Returns an
    error message describing the first invalid value, or None when all values
    are acceptable.

    ``current_timezone`` is the previously stored timezone. Legacy deployments
    may have a non-canonical value stored from before the UI restricted the
    select; an unchanged (even odd) timezone is left alone so it does not block
    saving unrelated settings. Only *new* timezone selections are validated.
    """
    try:
        page_size = int(values.get("page_size", 50))
        if not 10 <= page_size <= 500:
            return _("Items per page must be between 10 and 500.")
    except (TypeError, ValueError):
        return _("Items per page must be a number between 10 and 500.")

    try:
        retention_value = int(values.get("retention_value", 1))
        if not 1 <= retention_value <= 10000:
            return _("Retention period must be at least 1.")
    except (TypeError, ValueError):
        return _("Retention period must be a number of at least 1.")

    if values.get("retention_unit") not in ALLOWED_RETENTION_UNITS:
        return _("Invalid retention unit selected.")

    try:
        auto_logout_minutes = int(values.get("auto_logout_minutes", 60))
        if not 0 <= auto_logout_minutes <= 1440:
            return _("Auto logout must be between 0 and 1440 minutes (0 disables it).")
    except (TypeError, ValueError):
        return _("Auto logout must be a number of minutes.")

    try:
        clamav_port = int(values.get("clamav_port", 3310))
        if not 1 <= clamav_port <= 65535:
            return _("ClamAV port must be between 1 and 65535.")
    except (TypeError, ValueError):
        return _("ClamAV port must be a number.")

    try:
        clamav_max_file_size = int(values.get("clamav_max_file_size", 10485760))
        if not 1024 <= clamav_max_file_size <= 1073741824:
            return _("Max file size to scan must be at least 1024 bytes.")
    except (TypeError, ValueError):
        return _("Max file size to scan must be a number of bytes.")

    try:
        quarantine_days = int(values.get("clamav_quarantine_retention_days", 90))
        if not 1 <= quarantine_days <= 36500:
            return _("Quarantine retention must be at least 1 day.")
    except (TypeError, ValueError):
        return _("Quarantine retention must be a number of days.")

    if values.get("clamav_action") not in ALLOWED_CLAMAV_ACTIONS:
        return _("Invalid virus action selected.")

    try:
        smtp_port = int(values.get("smtp_port", 587))
        if not 1 <= smtp_port <= 65535:
            return _("SMTP port must be between 1 and 65535.")
    except (TypeError, ValueError):
        return _("SMTP port must be a number.")

    try:
        grace = int(values.get("clamav_failure_grace_seconds", 300))
        if not 0 <= grace <= 86400:
            return _("ClamAV alert grace period must be between 0 and 86400 seconds.")
    except (TypeError, ValueError):
        return _("ClamAV alert grace period must be a number of seconds.")

    timezone = values.get("timezone", "UTC")
    if timezone != current_timezone and timezone not in _KNOWN_TIMEZONES:
        return _("Unknown timezone: {0}").format(timezone)

    return None

router = APIRouter()


def flash(request: Request, message, category: str = "info"):
    request.session["flash"] = (
        message if isinstance(message, dict) else {"message": message, "type": category}
    )


# ---------------------------------------------------------
# GLOBAL SETTINGS FORM
# ---------------------------------------------------------
@router.get("/global-settings")
def settings_form(
    request: Request,
    _=require_permission(PERMISSIONS["manage_global_settings"])
):
    rows = query("SELECT key, value FROM settings").mappings().all()
    settings = {r["key"]: r["value"] for r in rows}

    # Sync global theme into session
    request.session["global_theme"] = settings.get("default_theme", "system")

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "global-settings.html",
        {"request": request, "settings": settings, "flash": msg},
    )


# ---------------------------------------------------------
# SAVE GLOBAL SETTINGS
# ---------------------------------------------------------
@router.post("/global-settings")
def save_settings(
    request: Request,
    _=require_permission(PERMISSIONS["manage_global_settings"]),
    page_size: int = Form(...),
    date_format: str = Form("%d %b %Y"),
    time_format: str = Form("%H:%M"),
    timezone: str = Form("UTC"),
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
    clamav_failure_grace_seconds: int = Form(300),
    smtp_enabled: bool = Form(False),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_use_tls: bool = Form(True),
    smtp_from_email: str = Form(""),
    smtp_from_name: str = Form("Daygle Mail Archiver"),
    auto_logout_minutes: int = Form(60),
):
    _ = request_gettext(request)

    # Load old settings
    rows = query("SELECT key, value FROM settings").mappings().all()
    old_settings = {r["key"]: r["value"] for r in rows}

    try:
        # Sanitize theme
        default_theme = default_theme if default_theme in ALLOWED_THEMES else "system"

        # Validate every submitted value before touching the database. In
        # particular, retention_value/clamav_quarantine_retention_days of 0 would
        # make the worker purge the entire archive on its next run.
        validation_values = {
            "page_size": page_size,
            "retention_value": retention_value,
            "retention_unit": retention_unit,
            "auto_logout_minutes": auto_logout_minutes,
            "clamav_port": clamav_port,
            "clamav_max_file_size": clamav_max_file_size,
            "clamav_quarantine_retention_days": clamav_quarantine_retention_days,
            "clamav_action": clamav_action,
            "smtp_port": smtp_port,
            "clamav_failure_grace_seconds": clamav_failure_grace_seconds,
            "timezone": timezone,
        }
        validation_error = validate_global_settings(
            validation_values,
            current_timezone=old_settings.get("timezone", "UTC"),
            _=_,
        )
        if validation_error:
            flash(request, validation_error, "error")
            return RedirectResponse("/global-settings", status_code=303)

        # Encrypt SMTP password before storage.
        # If the submitted password field is empty the user did not change it,
        # so keep the currently stored (already-encrypted) value.
        if smtp_password:
            try:
                from ..utils.crypto import encrypt_password
                smtp_password_stored = encrypt_password(smtp_password)
            except Exception as e:
                # Encryption unavailable (e.g. IMAP_PASSWORD_KEY not configured);
                # fall back to storing as-is so SMTP still functions, but warn the admin.
                log("warning", "Settings", f"SMTP password encryption failed; storing in plaintext: {e}")
                smtp_password_stored = smtp_password
        else:
            smtp_password_stored = old_settings.get("smtp_password", "")

        settings_data = [
            ("page_size", str(page_size)),
            ("date_format", date_format),
            ("time_format", time_format),
            ("timezone", timezone),
            ("default_theme", default_theme),
            ("enable_purge", str(enable_purge).lower()),
            ("retention_value", str(retention_value)),
            ("retention_unit", retention_unit),
            ("retention_delete_from_mail_server", str(retention_delete_from_mail_server).lower()),
            ("clamav_enabled", str(clamav_enabled).lower()),
            ("clamav_host", clamav_host),
            ("clamav_port", str(clamav_port)),
            ("clamav_action", clamav_action),
            ("clamav_quarantine_in_db", str(clamav_quarantine_in_db).lower()),
            ("clamav_quarantine_retention_days", str(clamav_quarantine_retention_days)),
            ("clamav_max_file_size", str(clamav_max_file_size)),
            ("clamav_quarantine_encrypt", str(clamav_quarantine_encrypt).lower()),
            ("clamav_failure_grace_seconds", str(clamav_failure_grace_seconds)),
            ("smtp_enabled", str(smtp_enabled).lower()),
            ("smtp_host", smtp_host),
            ("smtp_port", str(smtp_port)),
            ("smtp_username", smtp_username),
            ("smtp_password", smtp_password_stored),
            ("smtp_use_tls", str(smtp_use_tls).lower()),
            ("smtp_from_email", smtp_from_email),
            ("smtp_from_name", smtp_from_name),
            ("auto_logout_minutes", str(auto_logout_minutes)),
        ]

        new_settings = {k: v for (k, v) in settings_data}

        # Detect changes
        changed_keys = [k for k, v in new_settings.items() if old_settings.get(k) != v]
        if not changed_keys:
            flash(request, _("No changes detected."), "info")
            return RedirectResponse("/global-settings", status_code=303)

        # Apply updates atomically so a mid-save failure can never leave the
        # settings table with a partial (half-updated) configuration.
        with transaction() as conn:
            for key, value in settings_data:
                conn.execute(text("""
                    INSERT INTO settings (key, value)
                    VALUES (:key, :value)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """), {"key": key, "value": value})

        # Sync session
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
        request.session["global_theme"] = default_theme

        # Global date/time/timezone may have changed: clear every cached
        # preference answer so the new defaults apply immediately.
        invalidate_display_prefs()

        flash(request, _("Settings updated successfully."), "success")
        return RedirectResponse("/global-settings", status_code=303)

    except Exception as e:
        log("error", "Settings", f"Failed to save settings: {str(e)}")
        flash(request, _("Failed to save settings: {0}").format(str(e)), "error")
        return RedirectResponse("/global-settings", status_code=303)


# ---------------------------------------------------------
# TEST SMTP
# ---------------------------------------------------------
@router.post("/api/test-smtp")
def test_smtp(
    request: Request,
    _=require_permission(PERMISSIONS["manage_global_settings"])
):
    _ = request_gettext(request)
    try:
        user_id = request.session.get("user_id")
        user = query("""
            SELECT email
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        if not user or not user.get("email"):
            flash(request, _("Your account does not have an email address configured."), "error")
            return RedirectResponse("/global-settings", status_code=303)

        success, message = test_smtp_connection(user["email"], int(user_id))

        if success:
            flash(request, _("SMTP test successful: {0}").format(message), "success")
        else:
            flash(request, _("SMTP test failed: {0}").format(message), "error")

    except Exception as e:
        log("error", "Settings", f"SMTP test failed: {str(e)}")
        flash(request, _("SMTP test failed: {0}").format(str(e)), "error")

    return RedirectResponse("/global-settings", status_code=303)
