from fastapi import APIRouter, Request, Form, Body
from fastapi.responses import JSONResponse, RedirectResponse
import bcrypt
import re

from ..utils.db import query, execute
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.permissions import require_permission, PERMISSIONS
from ..utils.i18n import get_gettext
from ..utils.timezone import format_datetime

router = APIRouter()


def flash(request: Request, message, category: str = "info"):
    request.session["flash"] = (
        message if isinstance(message, dict) else {"message": message, "type": category}
    )


# ---------------------------------------------------------
# API: Get current user profile
# ---------------------------------------------------------
@router.get("/api/user/profile")
def get_user_profile(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"])
):
    user_id = request.session.get("user_id")
    permissions = request.session.get("permissions", [])

    return {
        "user_id": user_id,
        "permissions": permissions
    }


# ---------------------------------------------------------
# Profile page
# ---------------------------------------------------------
@router.get("/profile")
def profile_form(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"])
):
    user_id = request.session["user_id"]

    # Get auto-logout setting for online/offline calculation
    auto_logout_setting = query("SELECT value FROM settings WHERE key = 'auto_logout_minutes' ").mappings().first()
    auto_logout_minutes = int(auto_logout_setting["value"]) if auto_logout_setting else 60

    user = query("""
        SELECT username, first_name, last_name, email, last_login, last_login_ip, last_seen, created_at
        FROM users
        WHERE id = :id
    """, {"id": user_id}).mappings().first()

    # Fetch role display names assigned to the user
    role_rows = query("""
        SELECT COALESCE(r.display_name, r.name) AS role_name
        FROM roles r
        JOIN user_roles ur ON r.id = ur.role_id
        WHERE ur.user_id = :id
        ORDER BY role_name
    """, {"id": user_id}).mappings().all()
    roles = [r["role_name"] for r in role_rows] if role_rows else []

    # Compute online status using same logic as users list
    online_status = 'offline'
    if user and user.get("last_seen"):
        now_check = query("SELECT CASE WHEN NOW() - :last_seen > INTERVAL ':minutes minutes' THEN 'offline' ELSE 'online' END AS status", {"last_seen": user["last_seen"], "minutes": auto_logout_minutes}).mappings().first()
        online_status = now_check["status"]

    msg = request.session.pop("flash", None)

    # Format datetimes for display without mutating RowMapping
    current_user_id = int(request.session.get("user_id"))
    last_login_fmt = format_datetime(user["last_login"], current_user_id) if user and user.get("last_login") else None
    last_seen_fmt = format_datetime(user["last_seen"], current_user_id) if user and user.get("last_seen") else None
    created_at_fmt = format_datetime(user["created_at"], current_user_id) if user and user.get("created_at") else None

    user_payload = {
        "username": user.get("username") if user else None,
        "first_name": user.get("first_name") if user else None,
        "last_name": user.get("last_name") if user else None,
        "email": user.get("email") if user else None,
        "roles": roles,
        "last_login": last_login_fmt,
        "last_login_ip": user.get("last_login_ip") if user else None,
        "last_seen": last_seen_fmt,
        "online_status": online_status,
        "created_at": created_at_fmt
    }

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "flash": msg,
        "user": user_payload
    })


# ---------------------------------------------------------
# Change password
# ---------------------------------------------------------
@router.post("/profile/change-password")
def change_password(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"]),
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")

    user = query("""
        SELECT password_hash
        FROM users
        WHERE id = :id
    """, {"id": user_id}).mappings().first()

    if not user:
        flash(request, "User not found.", "error")
        return RedirectResponse("/change-password", status_code=303)

    # Verify current password
    try:
        if not bcrypt.checkpw(current_password.encode("utf-8"), user["password_hash"].encode("utf-8")):
            log("warning", "Security", f"User '{username}' failed password change - incorrect current password")
            flash(request, "Current password is incorrect.", "error")
            return RedirectResponse("/change-password", status_code=303)
    except Exception as e:
        log("error", "Security", f"Password verification error for '{username}': {str(e)}")
        flash(request, "An error occurred. Please try again.", "error")
        return RedirectResponse("/change-password", status_code=303)

    # Validate new password
    if new_password != confirm_password:
        flash(request, "New passwords do not match.", "error")
        return RedirectResponse("/change-password", status_code=303)

    if (
        len(new_password) < 8
        or not re.search(r"[a-z]", new_password)
        or not re.search(r"[A-Z]", new_password)
        or not re.search(r"[0-9]", new_password)
    ):
        flash(request, "Password must include upper, lower, number and be 8+ chars.", "error")
        return RedirectResponse("/change-password", status_code=303)

    try:
        hash_pw = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        execute("""
            UPDATE users
            SET password_hash = :h
            WHERE id = :id
        """, {"h": hash_pw, "id": user_id})

        log("warning", "Security", f"User '{username}' changed their password")
        flash(request, "Password changed successfully.", "success")
        return RedirectResponse("/change-password", status_code=303)

    except Exception as e:
        log("error", "Security", f"Failed to update password for '{username}': {str(e)}")
        flash(request, "Failed to update password. Please try again.", "error")
        return RedirectResponse("/change-password", status_code=303)


# ---------------------------------------------------------
# Change password form
# ---------------------------------------------------------
@router.get("/change-password")
def change_password_form(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"])
):
    user_id = request.session["user_id"]

    user = query("""
        SELECT username, first_name, last_name, email
        FROM users
        WHERE id = :id
    """, {"id": user_id}).mappings().first()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse("change-password.html", {
        "request": request,
        "flash": msg,
        "user": user
    })


# ---------------------------------------------------------
# Update profile info
# ---------------------------------------------------------
@router.post("/profile/update-info")
def update_info(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"]),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form("")
):
    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")

    # Validate email
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.", "error")
        return RedirectResponse("/profile", status_code=303)

    try:
        current = query("""
            SELECT first_name, last_name, email
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        new_fn = first_name.strip() or None
        new_ln = last_name.strip() or None
        new_e = email.strip() or None

        if (
            current
            and current.get("first_name") == new_fn
            and current.get("last_name") == new_ln
            and (current.get("email") or None) == new_e
        ):
            flash(request, "No changes detected.", "info")
            return RedirectResponse("/profile", status_code=303)

        execute("""
            UPDATE users
            SET first_name = :fn, last_name = :ln, email = :e
            WHERE id = :id
        """, {"fn": new_fn, "ln": new_ln, "e": new_e, "id": user_id})

        log("info", "Profile", f"User '{username}' updated their profile")
        flash(request, "Profile updated successfully.", "success")
        return RedirectResponse("/profile", status_code=303)

    except Exception as e:
        log("error", "Profile", f"Failed to update profile for '{username}': {str(e)}")
        flash(request, "Failed to update profile. Please try again.", "error")
        return RedirectResponse("/profile", status_code=303)


# ---------------------------------------------------------
# User settings page
# ---------------------------------------------------------
@router.get("/user-settings")
def user_settings_form(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"])
):
    user_id = request.session["user_id"]

    user = query("""
        SELECT page_size, date_format, time_format, timezone,
               theme_preference, email_notifications, avatar_color, language
        FROM users
        WHERE id = :id
    """, {"id": user_id}).mappings().first()

    # Defaults
    current_page_size = user["page_size"] if user else 50
    current_date_format = user["date_format"] if user else "%d/%m/%Y"
    current_time_format = user["time_format"] if user else "%H:%M"
    current_timezone = user["timezone"] or "Australia/Melbourne"
    current_theme = user.get("theme_preference") or "system"
    current_email_notifications = user["email_notifications"]
    current_avatar_color = user.get("avatar_color") or "#007bff"
    current_language = user.get("language") or "en"

    # Sync session
    request.session["page_size"] = current_page_size
    request.session["date_format"] = current_date_format
    request.session["time_format"] = current_time_format
    request.session["timezone"] = current_timezone
    request.session["theme"] = current_theme
    request.session["avatar_color"] = current_avatar_color
    request.session["language"] = current_language

    msg = request.session.pop("flash", None)

    lang = request.session.get('language', 'en') if "session" in request.scope else 'en'
    _ = get_gettext(lang)

    return templates.TemplateResponse("user-settings.html", {
        "request": request,
        "flash": msg,
        "page_size": current_page_size,
        "date_format": current_date_format,
        "time_format": current_time_format,
        "timezone": current_timezone,
        "theme": current_theme,
        "avatar_color": current_avatar_color,
        "email_notifications": current_email_notifications,
        "language": current_language,
        "gettext": _
    })


# ---------------------------------------------------------
# Update user settings
# ---------------------------------------------------------
@router.post("/user-settings/update")
def update_user_settings(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"]),
    page_size: int = Form(...),
    date_format: str = Form(...),
    time_format: str = Form(...),
    timezone: str = Form("Australia/Melbourne"),
    theme: str = Form("system"),
    avatar_color: str = Form("#007bff"),
    email_notifications: bool = Form(True),
    language: str = Form("en")
):
    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")

    # Validate page_size
    if page_size < 10 or page_size > 500:
        flash(request, "Items per page must be between 10 and 500.", "error")
        return RedirectResponse("/user-settings", status_code=303)

    try:
        current = query("""
            SELECT page_size, date_format, time_format, timezone,
                   theme_preference, email_notifications, avatar_color, language
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        changed = []

        if current["page_size"] != page_size:
            changed.append(f"page_size={page_size}")
        if current["date_format"] != date_format:
            changed.append(f"date_format={date_format}")
        if current["time_format"] != time_format:
            changed.append(f"time_format={time_format}")
        if current["timezone"] != timezone:
            changed.append(f"timezone={timezone}")
        if current.get("theme_preference") != theme:
            changed.append(f"theme={theme}")
        if current.get("avatar_color") != avatar_color:
            changed.append(f"avatar_color={avatar_color}")
        if current["email_notifications"] != email_notifications:
            changed.append(f"email_notifications={email_notifications}")
        if current.get("language") != language:
            changed.append(f"language={language}")

        if not changed:
            flash(request, "No changes detected.", "info")
            return RedirectResponse("/user-settings", status_code=303)

        execute("""
            UPDATE users
            SET page_size = :ps, date_format = :df, time_format = :tf,
                timezone = :tz, theme_preference = :theme,
                avatar_color = :ac, email_notifications = :en, language = :lang
            WHERE id = :id
        """, {
            "ps": page_size,
            "df": date_format,
            "tf": time_format,
            "tz": timezone,
            "theme": theme,
            "ac": avatar_color,
            "en": email_notifications,
            "lang": language,
            "id": user_id
        })

        # Sync session
        request.session["page_size"] = page_size
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
        request.session["theme"] = theme
        request.session["avatar_color"] = avatar_color
        request.session["language"] = language

        log("info", "Settings", f"User '{username}' updated settings ({', '.join(changed)})")
        flash(request, "User settings updated successfully.", "success")
        return RedirectResponse("/user-settings", status_code=303)

    except Exception as e:
        log("error", "Settings", f"Failed to update settings for '{username}': {str(e)}")
        flash(request, "Failed to update settings. Please try again.", "error")
        return RedirectResponse("/user-settings", status_code=303)


# ---------------------------------------------------------
# Set theme (AJAX)
# ---------------------------------------------------------
@router.post("/api/user/theme")
def set_user_theme(
    request: Request,
    _=require_permission(PERMISSIONS["manage_own_profile"]),
    payload: dict = Body(...)
):
    theme = payload.get("theme")

    if theme not in ("light", "dark", "system"):
        return JSONResponse({"error": "Invalid theme"}, status_code=400)

    user_id = request.session["user_id"]

    try:
        execute("""
            UPDATE users
            SET theme_preference = :theme
            WHERE id = :id
        """, {"theme": theme, "id": user_id})
    except Exception as e:
        log("error", "Settings", f"Failed to persist theme for user {user_id}: {str(e)}")

    request.session["theme"] = theme
    return JSONResponse({"status": "ok"})
