from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
import bcrypt
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import List

from ..utils.db import query, execute
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.email import send_email
from ..utils.permissions import PermissionChecker
from ..utils.i18n import get_gettext

router = APIRouter()


def get_client_ip(request: Request) -> str:
    # Check X-Forwarded-For first (may contain multiple IPs)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First IP in the list is the real client
        return forwarded.split(",")[0].strip()
    
    # Fallback to direct client IP
    return request.client.host


def load_user_permissions(user_id: int) -> List[str]:
    """Load permissions strictly from the new RBAC system."""
    try:
        rows = query("""
            SELECT DISTINCT p.name
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = :user_id
        """, {"user_id": user_id}).mappings().all()

        return [r["name"] for r in rows]

    except Exception as e:
        log("error", "Permissions", f"Failed to load permissions for user {user_id}: {str(e)}")
        return []


def is_setup_complete():
    """Check if initial setup has been completed."""
    try:
        result = query("SELECT value FROM settings WHERE key = 'setup_complete'").mappings().first()
        return result and result["value"] == "true"
    except Exception:
        return False


# ---------------------------------------------------------
# SETUP WIZARD
# ---------------------------------------------------------
@router.get("/setup")
def setup_wizard_form(request: Request):
    if is_setup_complete():
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse("setup.html", {"request": request})


@router.post("/setup")
def setup_wizard_submit(
    request: Request,
    username: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    if is_setup_complete():
        return RedirectResponse("/login", status_code=303)

    # Username validation
    if len(username) < 3:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "Username must be at least 3 characters long",
             "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )

    # Unique username
    existing_user = query("SELECT id FROM users WHERE username = :u", {"u": username}).mappings().first()
    if existing_user:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "Username already exists",
             "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )

    # Password validation
    if password != confirm_password:
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "Passwords do not match",
             "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )

    if (
        len(password) < 8
        or not re.search(r"[a-z]", password)
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[0-9]", password)
    ):
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "Password must include upper, lower, number and be 8+ chars",
             "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )

    try:
        # Create admin user
        hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        new_user = query("""
            INSERT INTO users (username, password_hash, first_name, last_name, email)
            VALUES (:u, :h, :fn, :ln, :e)
            RETURNING id
        """, {
            "u": username,
            "h": hash_pw,
            "fn": first_name or None,
            "ln": last_name or None,
            "e": email or None
        }).mappings().first()

        user_id = new_user["id"]

        # Assign administrator role based on RBAC permission (manage_users)
        admin_role = query("""
            SELECT r.id
            FROM roles r
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id
            WHERE p.name = 'manage_users'
        """).mappings().first()

        if admin_role:
            execute("""
                INSERT INTO user_roles (user_id, role_id)
                VALUES (:user_id, :role_id)
            """, {"user_id": user_id, "role_id": admin_role["id"]})

        # Mark setup complete
        execute("UPDATE settings SET value = 'true' WHERE key = 'setup_complete'")

        log("info", "Setup", f"Initial setup completed - Administrator '{username}' created")
        return RedirectResponse("/login?setup_complete=true", status_code=303)

    except Exception as e:
        log("error", "Setup", f"Failed to create administrator account: {str(e)}")
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "Failed to create account. Please try again.",
             "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )


# ---------------------------------------------------------
# LOGIN
# ---------------------------------------------------------
@router.get("/login")
def login_form(request: Request, setup_complete: str = ""):
    if not is_setup_complete():
        return RedirectResponse("/setup", status_code=303)

    success_message = (
        "Setup complete! Please login with your new account."
        if setup_complete == "true" else None
    )

    return templates.TemplateResponse("login.html", {"request": request, "success": success_message})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), language: str = Form('en')):
    # Ensure selected language takes effect immediately for the response
    try:
        if "session" in request.scope:
            request.session["language"] = language or request.session.get("language", "en")
    except Exception:
        pass

    # Debug logging for login attempts removed
    try:
        user = query("""
            SELECT id, username, password_hash, date_format, time_format,
                   timezone, theme_preference, enabled,
                   failed_login_attempts, locked_until, avatar_color, language
            FROM users
            WHERE username = :u
        """, {"u": username}).mappings().first()
    except Exception as e:
        log("error", "Login", f"Database error during login for {username}: {str(e)}")
        return templates.TemplateResponse("login.html", {"request": request, "error": "System error. Please try again."})

    if not user:
        try:
            log("warning", "Login", f"Failed login attempt for unknown user: {username}")
        except Exception:
            pass
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials"})

    # Account lockout
    now = query("SELECT NOW() AS now").mappings().first()["now"]
    if user["locked_until"] and user["locked_until"] > now:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Account is temporarily locked. Use 'Forgot Password' to unlock."},
        )

    if not user["enabled"]:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "This account has been disabled"},
        )

    # First login (no password set)
    if not user["password_hash"]:
        client_ip = get_client_ip(request)
        execute("""
            UPDATE users
            SET last_login = NOW(), last_login_ip = :ip
            WHERE id = :id
        """, {"id": user["id"], "ip": client_ip})
        try:
            execute("""
                UPDATE users
                SET last_seen = NOW()
                WHERE id = :id
            """, {"id": user["id"]})
        except Exception:
            pass
        request.session["user_id"] = user["id"]
        request.session["username"] = user["username"]
        request.session["date_format"] = user["date_format"] or "%d/%m/%Y"
        request.session["time_format"] = user["time_format"] or "%H:%M"
        request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
        request.session["theme"] = user.get("theme_preference") or "system"
        request.session["avatar_color"] = user.get("avatar_color") or "#007bff"
        # Persist selected language into session and user record
        request.session["language"] = language or (user.get("language") or "en")
        try:
            execute("""
                UPDATE users
                SET language = :lang
                WHERE id = :id
            """, {"lang": request.session["language"], "id": user["id"]})
        except Exception:
            # Non-fatal: login can proceed even if language persistence fails
            pass
        request.session["needs_password"] = True
        request.session["permissions"] = load_user_permissions(user["id"])
        return RedirectResponse("/set-password", status_code=303)

    # Normal login
    try:
        pw_hash = user.get("password_hash")
        ok = False
        if pw_hash:
            ok = bcrypt.checkpw(password.encode("utf-8"), pw_hash.encode("utf-8"))
        if ok:
            client_ip = get_client_ip(request)
            execute("""
                UPDATE users
                SET failed_login_attempts = 0, locked_until = NULL, last_login = NOW(), last_login_ip = :ip
                WHERE id = :id
            """, {"id": user["id"], "ip": client_ip})
            try:
                execute("""
                    UPDATE users
                    SET last_seen = NOW()
                    WHERE id = :id
                """, {"id": user["id"]})
            except Exception:
                pass

            request.session["user_id"] = user["id"]
            request.session["username"] = user["username"]
            request.session["date_format"] = user["date_format"] or "%d/%m/%Y"
            request.session["time_format"] = user["time_format"] or "%H:%M"
            request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
            request.session["theme"] = user.get("theme_preference") or "system"
            request.session["avatar_color"] = user.get("avatar_color") or "#007bff"
            request.session["permissions"] = load_user_permissions(user["id"])
            # Persist selected language into session and user record
            request.session["language"] = language or (user.get("language") or "en")
            try:
                execute("""
                    UPDATE users
                    SET language = :lang
                    WHERE id = :id
                """, {"lang": request.session["language"], "id": user["id"]})
            except Exception:
                pass

            pass
            return RedirectResponse("/dashboard", status_code=303)

    except Exception as e:
        try:
            log("error", "Login", f"Password verification error for {username}: {str(e)}")
        except Exception:
            pass
        return templates.TemplateResponse("login.html", {"request": request, "error": "System error. Please try again."})

    # Failed login
    new_attempts = user["failed_login_attempts"] + 1
    max_attempts = 5
    lock_minutes = 15

    if new_attempts >= max_attempts:
        execute("""
            UPDATE users
            SET failed_login_attempts = :a, locked_until = NOW() + INTERVAL ':m minutes'
            WHERE id = :id
        """, {"a": new_attempts, "m": lock_minutes, "id": user["id"]})

        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Account locked. Try again in {lock_minutes} minutes."},
        )

    execute("""
        UPDATE users
        SET failed_login_attempts = :a
        WHERE id = :id
    """, {"a": new_attempts, "id": user["id"]})

    remaining = max_attempts - new_attempts
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": f"Invalid credentials. {remaining} attempts remaining."},
    )


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------
@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.post("/set-language")
async def set_language(request: Request):
    """Allow unauthenticated users to set a preferred language into the session.
    If the user is authenticated, persist it to their DB record too.
    """
    try:
        data = await request.json()
        language = data.get('language', 'en')
    except Exception as e:
        try:
            log("error", "Language", f"set-language JSON error: {str(e)}")
        except Exception:
            pass
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    try:
        if "session" in request.scope:
            request.session["language"] = language
    except Exception as e:
        try:
            log("error", "Language", f"session set error: {str(e)}")
        except Exception:
            pass

    # If logged in, persist preference to DB (best-effort)
    try:
        user_id = request.session.get("user_id") if "session" in request.scope else None
        if user_id:
            execute("""
                UPDATE users
                SET language = :lang
                WHERE id = :id
            """, {"lang": language, "id": user_id})
    except Exception as e:
        try:
            log("error", "Language", f"DB update error: {str(e)}")
        except Exception:
            pass

    return JSONResponse({"success": True})


# ---------------------------------------------------------
# FORGOT / RESET PASSWORD
# ---------------------------------------------------------
@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse("forgot-password.html", {"request": request})


@router.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(...)):
    # Generate a reset token and send email if the account exists.
    try:
        user = query("SELECT id, username, email FROM users WHERE lower(email) = lower(:e)", {"e": email}).mappings().first()
    except Exception:
        user = None

    if user:
        try:
            token = secrets.token_urlsafe(32)
            # expiry 1 hour from now
            execute("""
                UPDATE users
                SET password_reset_token = :t, password_reset_expires = NOW() + INTERVAL '1 hour'
                WHERE id = :id
            """, {"t": token, "id": user["id"]})

            base = str(request.base_url).rstrip('/')
            reset_link = f"{base}/reset-password?token={token}"
            subject = "Reset your Daygle password"
            body = (
                f"Hello {user.get('username') or ''},\n\n"
                f"A request was received to reset your Daygle Mail Archiver password. "
                f"If you did not request this, you can ignore this message.\n\n"
                f"To reset your password, visit the following link:\n{reset_link}\n\n"
                "This link will expire in 1 hour.\n\n"
                "If you have trouble, contact your administrator.\n"
            )
            # best-effort send; failures are non-fatal
            try:
                send_email(user.get('email'), subject, body)
            except Exception:
                pass
        except Exception:
            # swallow DB/email errors and show generic response
            pass

    # Always show the same message to avoid user enumeration
    return templates.TemplateResponse(
        "forgot-password.html",
        {"request": request, "success": "If an account exists for that email, a reset link has been sent."},
    )


@router.get("/reset-password")
def reset_password_form(request: Request, token: str = ""):
    if not token:
        return RedirectResponse("/login", status_code=303)

    try:
        row = query(
            "SELECT id FROM users WHERE password_reset_token = :t AND password_reset_expires > NOW()",
            {"t": token},
        ).mappings().first()
    except Exception:
        row = None

    if not row:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid or expired reset token."})

    return templates.TemplateResponse("reset-password.html", {"request": request, "token": token})


@router.post("/reset-password")
def reset_password_submit(request: Request, token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if password != confirm_password:
        return templates.TemplateResponse("reset-password.html", {"request": request, "error": "Passwords do not match.", "token": token})

    if len(password) < 8 or not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password) or not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "error": "Password must include upper, lower, number and be 8+ chars", "token": token},
        )

    try:
        user = query(
            "SELECT id FROM users WHERE password_reset_token = :t AND password_reset_expires > NOW()",
            {"t": token},
        ).mappings().first()
    except Exception:
        user = None

    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid or expired reset token."})

    # Update password and clear token
    try:
        hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        execute(
            """
            UPDATE users
            SET password_hash = :h, password_reset_token = NULL, password_reset_expires = NULL, failed_login_attempts = 0, locked_until = NULL
            WHERE id = :id
            """,
            {"h": hash_pw, "id": user["id"]},
        )
    except Exception:
        return templates.TemplateResponse("reset-password.html", {"request": request, "error": "Failed to reset password. Please try again.", "token": token})

    return templates.TemplateResponse("login.html", {"request": request, "success": "Password reset successful. Please login with your new password."})