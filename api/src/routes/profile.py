from fastapi import APIRouter, Request, Form
from fastapi.responses import JSONResponse, RedirectResponse
import bcrypt
import re

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/api/user/profile")
def get_user_profile(request: Request):
    """Get current user profile information"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    
    user_id = request.session.get("user_id")
    role = request.session.get("role", "user")
    
    return {
        "user_id": user_id,
        "role": role
    }

@router.get("/profile")
def profile_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    user = query("""
        SELECT username, first_name, last_name, email, last_login, created_at 
        FROM users WHERE id = :id
    """, {"id": user_id}).mappings().first()

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("profile.html", {
        "request": request, 
        "flash": msg, 
        "user": user
    })

@router.post("/profile/change-password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")
    
    user = query("SELECT password_hash FROM users WHERE id = :id", {"id": user_id}).mappings().first()

    if not user:
        flash(request, "User not found.")
        return RedirectResponse("/profile", status_code=303)
    
    # Verify current password
    try:
        if not bcrypt.checkpw(current_password.encode('utf-8'), user["password_hash"].encode('utf-8')):
            log("warning", "Security", f"User '{username}' failed password change - incorrect current password", "")
            flash(request, "Current password is incorrect.")
            return RedirectResponse("/profile", status_code=303)
    except Exception as e:
        log("error", "Security", f"Password verification error for user '{username}': {str(e)}", "")
        flash(request, "An error occurred. Please try again.")
        return RedirectResponse("/profile", status_code=303)

    # Validate new password
    if new_password != confirm_password:
        flash(request, "New passwords do not match.")
        return RedirectResponse("/profile", status_code=303)

    if len(new_password) < 8:
        flash(request, "New password must be at least 8 characters long.")
        return RedirectResponse("/profile", status_code=303)
    
    # Check password complexity
    if not re.search(r"[a-z]", new_password):
        flash(request, "Password must contain at least one lowercase letter.")
        return RedirectResponse("/profile", status_code=303)
    
    if not re.search(r"[A-Z]", new_password):
        flash(request, "Password must contain at least one uppercase letter.")
        return RedirectResponse("/profile", status_code=303)
    
    if not re.search(r"[0-9]", new_password):
        flash(request, "Password must contain at least one number.")
        return RedirectResponse("/profile", status_code=303)

    try:
        hash_pw = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        execute("UPDATE users SET password_hash = :h WHERE id = :id", {"h": hash_pw, "id": user_id})
        
        log("warning", "Security", f"User '{username}' successfully changed their password", "")
        flash(request, "Password changed successfully.")
        return RedirectResponse("/profile", status_code=303)
    except Exception as e:
        log("error", "Security", f"Failed to update password for user '{username}': {str(e)}", "")
        flash(request, "Failed to update password. Please try again.")
        return RedirectResponse("/profile", status_code=303)

@router.post("/profile/update-info")
def update_info(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form("")
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")
    
    # Validate email format if provided
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.")
        return RedirectResponse("/profile", status_code=303)
    
    try:
        execute("""
            UPDATE users 
            SET first_name = :fn, last_name = :ln, email = :e 
            WHERE id = :id
        """, {"fn": first_name.strip(), "ln": last_name.strip(), "e": email.strip(), "id": user_id})
        
        log("info", "Profile", f"User '{username}' updated their profile information", "")
        flash(request, "Profile updated successfully.")
        return RedirectResponse("/profile", status_code=303)
    except Exception as e:
        log("error", "Profile", f"Failed to update profile for user '{username}': {str(e)}", "")
        flash(request, "Failed to update profile. Please try again.")
        return RedirectResponse("/profile", status_code=303)

@router.get("/user-settings")
def user_settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    user = query("SELECT page_size, date_format, time_format, timezone, theme_preference, email_notifications, role FROM users WHERE id = :id", {"id": user_id}).mappings().first()
    current_page_size = user["page_size"] if user else 50
    current_date_format = user["date_format"] if user else "%d/%m/%Y"
    current_time_format = user["time_format"] if user else "%H:%M"
    current_timezone = user["timezone"] if user and user["timezone"] else "Australia/Melbourne"
    current_theme = user["theme_preference"] if user and user.get("theme_preference") else "system"
    current_email_notifications = user["email_notifications"] if user else True
    user_role = user["role"] if user else "administrator"

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("user-settings.html", {
        "request": request, 
        "flash": msg,
        "page_size": current_page_size,
        "date_format": current_date_format,
        "time_format": current_time_format,
        "timezone": current_timezone,
        "theme": current_theme,
        "email_notifications": current_email_notifications,
        "user_role": user_role
    })

@router.post("/user-settings/update")
def update_user_settings(request: Request, page_size: int = Form(...), date_format: str = Form(...), time_format: str = Form(...), timezone: str = Form("Australia/Melbourne"), theme: str = Form("system"), email_notifications: bool = Form(True)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")
    
    # Get user role
    user = query("SELECT role FROM users WHERE id = :id", {"id": user_id}).mappings().first()
    user_role = user["role"] if user else "administrator"
    
    # Validate page_size
    if page_size < 10 or page_size > 500:
        flash(request, "Items per page must be between 10 and 500.")
        return RedirectResponse("/user-settings", status_code=303)
    
    try:
        # Get current settings for comparison
        current_settings = query("SELECT page_size, date_format, time_format, timezone, theme_preference, email_notifications FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        
        # Only update email_notifications for administrators
        if user_role == "administrator":
            execute("UPDATE users SET page_size = :ps, date_format = :df, time_format = :tf, timezone = :tz, theme_preference = :theme, email_notifications = :en WHERE id = :id", 
                    {"ps": page_size, "df": date_format, "tf": time_format, "tz": timezone, "theme": theme, "en": email_notifications, "id": user_id})
        else:
            execute("UPDATE users SET page_size = :ps, date_format = :df, time_format = :tf, timezone = :tz, theme_preference = :theme WHERE id = :id", 
                    {"ps": page_size, "df": date_format, "tf": time_format, "tz": timezone, "theme": theme, "id": user_id})
        
        # Update session variables
        request.session["page_size"] = page_size
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
        request.session["theme"] = theme
        
        # Log only changed values
        changed_settings = []
        if current_settings:
            if current_settings["page_size"] != page_size:
                changed_settings.append(f"page_size={page_size}")
            if current_settings["date_format"] != date_format:
                changed_settings.append(f"date_format={date_format}")
            if current_settings["time_format"] != time_format:
                changed_settings.append(f"time_format={time_format}")
            if current_settings["timezone"] != timezone:
                changed_settings.append(f"timezone={timezone}")
            if current_settings.get("theme_preference") != theme:
                changed_settings.append(f"theme={theme}")
            if user_role == "administrator" and current_settings["email_notifications"] != email_notifications:
                changed_settings.append(f"email_notifications={email_notifications}")
        
        if changed_settings:
            log("info", "Settings", f"User '{username}' updated their settings ({', '.join(changed_settings)})", "")
        
        flash(request, "User settings updated successfully.")
        return RedirectResponse("/user-settings", status_code=303)
    except Exception as e:
        log("error", "Settings", f"Failed to update settings for user '{username}': {str(e)}", "")
        flash(request, "Failed to update settings. Please try again.")
        return RedirectResponse("/user-settings", status_code=303)