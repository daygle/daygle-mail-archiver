from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
import bcrypt
import re
import secrets
import hashlib
from datetime import datetime, timedelta
from typing import List

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates
from utils.email import send_email
from utils.permissions import PermissionChecker

router = APIRouter()

def load_user_permissions(user_id: int) -> List[str]:
    """Load all permissions for a user"""
    try:
        permissions = query("""
            SELECT DISTINCT p.name
            FROM permissions p
            JOIN role_permissions rp ON p.id = rp.permission_id
            JOIN user_roles ur ON rp.role_id = ur.role_id
            WHERE ur.user_id = :user_id
        """, {"user_id": user_id}).mappings().all()

        user_permissions = [p["name"] for p in permissions]

        # If user has permissions from roles, return them
        if user_permissions:
            return user_permissions

        # Fallback: Check old role field for backward compatibility
        user = query("SELECT role FROM users WHERE id = :user_id", {"user_id": user_id}).first()
        if user and user["role"]:
            if user["role"] == "administrator":
                # Administrator gets all permissions
                all_permissions = query("SELECT name FROM permissions").mappings().all()
                return [p["name"] for p in all_permissions]
            elif user["role"] == "read_only":
                # Read-only gets basic view permissions
                return [
                    "view_dashboard", "view_emails", "view_reports",
                    "view_quarantine", "view_fetch_accounts", "view_worker_status",
                    "view_logs", "view_alerts", "view_users", "view_global_settings",
                    "manage_own_profile"
                ]

        return []
    except Exception as e:
        log("error", "Permissions", f"Failed to load permissions for user {user_id}: {str(e)}")
        return []

def is_setup_complete():
    """Check if initial setup has been completed"""
    try:
        result = query("SELECT value FROM settings WHERE key = 'setup_complete'").mappings().first()
        return result and result["value"] == "true"
    except Exception:
        return False

@router.get("/setup")
def setup_wizard_form(request: Request):
    """Initial setup wizard - only accessible if setup not complete"""
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
    """Process initial setup wizard submission"""
    if is_setup_complete():
        return RedirectResponse("/login", status_code=303)
    
    # Validate username
    if not username or len(username) < 3:
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Username must be at least 3 characters long", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Check if username already exists
    try:
        existing_user = query("SELECT id FROM users WHERE username = :u", {"u": username}).mappings().first()
        if existing_user:
            return templates.TemplateResponse(
                "setup.html",
                {"request": request, "error": "Username already exists", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
            )
    except Exception as e:
        log("error", "Setup", f"Database error checking username: {str(e)}")
        return templates.TemplateResponse(
            "setup.html",
            {"request": request, "error": "System error. Please try again.", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Validate password matches confirmation
    if password != confirm_password:
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Passwords do not match", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Validate password strength
    if len(password) < 8:
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Password must be at least 8 characters long", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[a-z]", password):
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Password must contain at least one lowercase letter", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[A-Z]", password):
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Password must contain at least one uppercase letter", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Password must contain at least one number", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Create the administrator account
    try:
        hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        # Use RETURNING to get the inserted user id
        new_user = query(
            "INSERT INTO users (username, password_hash, first_name, last_name, email) VALUES (:username, :password_hash, :first_name, :last_name, :email) RETURNING id",
            {
                "username": username,
                "password_hash": hash_pw,
                "first_name": first_name if first_name else None,
                "last_name": last_name if last_name else None,
                "email": email if email else None
            }
        ).mappings().first()
        user_id = new_user["id"] if new_user else None
        
        # Assign the user to the administrator role
        try:
            admin_role = query("SELECT id FROM roles WHERE name = 'administrator'").first()
            if admin_role:
                execute(
                    "INSERT INTO user_roles (user_id, role_id) VALUES (:user_id, :role_id)",
                    {"user_id": user_id, "role_id": admin_role["id"]}
                )
        except Exception as e:
            log("warning", "Setup", f"Could not assign administrator role to first user: {str(e)}")
        
        # Mark setup as complete
        execute(
            "UPDATE settings SET value = 'true' WHERE key = 'setup_complete'"
        )
        
        log("info", "Setup", f"Initial setup completed - Administrator account '{username}' created")
        
        # Redirect to login page
        return RedirectResponse("/login?setup_complete=true", status_code=303)
        
    except Exception as e:
        log("error", "Setup", f"Failed to create administrator account: {str(e)}")
        return templates.TemplateResponse(
"setup.html",
            {"request": request, "error": "Failed to create account. Please try again.", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )

@router.get("/login")
def login_form(request: Request, setup_complete: str = ""):
    """Login form - redirects to setup if not complete"""
    if not is_setup_complete():
        return RedirectResponse("/setup", status_code=303)
    
    # Show success message if coming from setup
    success_message = "Setup complete! Please login with your new account." if setup_complete == "true" else None
    
    return templates.TemplateResponse("login.html", {"request": request, "success": success_message})

@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        user = query(
            "SELECT id, username, password_hash, date_format, time_format, timezone, theme_preference, role, enabled, failed_login_attempts, locked_until FROM users WHERE username = :u",
            {"u": username}
        ).mappings().first()
    except Exception as e:
        log("error", "Login", f"Database error during login for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "System error. Please try again."},
        )

    if not user:
        log("warning", "Login", f"Failed login attempt for unknown user: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
        )

    # Check if account is locked
    if user["locked_until"] and user["locked_until"] > query("SELECT NOW()").scalar():
        log("warning", "Login", f"Login attempt for locked account: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Account is temporarily locked due to too many failed login attempts. Use 'Forgot Password' to unlock your account."},
        )

    if not user["enabled"]:
        log("warning", "Login", f"Failed login attempt for disabled user: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "This account has been disabled"},
        )

    if not user["password_hash"]:
        # First login, set password
        request.session["user_id"] = user["id"]
        request.session["username"] = user["username"]
        request.session["date_format"] = user["date_format"] or "%d/%m/%Y"
        request.session["time_format"] = user["time_format"] or "%H:%M"
        request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
        request.session["theme"] = user.get("theme_preference") or "system"
        # Load global default theme into session for use when user preference == 'system'
        try:
            g = query("SELECT value FROM settings WHERE key = 'default_theme'").mappings().first()
            request.session["global_theme"] = g["value"] if g and g.get("value") else "system"
        except Exception:
            request.session["global_theme"] = "system"
        request.session["permissions"] = load_user_permissions(user["id"])
        request.session["needs_password"] = True
        log("info", "Login", f"User {username} initiated first login")
        return RedirectResponse("/set-password", status_code=303)

    try:
        if bcrypt.checkpw(password.encode('utf-8'), user["password_hash"].encode('utf-8')):
            # Successful login - reset failed attempts and unlock account
            execute("UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = :id", {"id": user["id"]})
            
            request.session["user_id"] = user["id"]
            request.session["username"] = user["username"]
            request.session["date_format"] = user["date_format"] or "%d/%m/%Y"
            request.session["time_format"] = user["time_format"] or "%H:%M"
            request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
            request.session["role"] = user["role"] or "administrator"
            request.session["theme"] = user.get("theme_preference") or "system"
            # Load global default theme into session for use when user preference == 'system'
            try:
                g = query("SELECT value FROM settings WHERE key = 'default_theme'").mappings().first()
                request.session["global_theme"] = g["value"] if g and g.get("value") else "system"
            except Exception:
                request.session["global_theme"] = "system"
            request.session["permissions"] = load_user_permissions(user["id"])
            
            # Update last_login timestamp
            try:
                execute("UPDATE users SET last_login = NOW() WHERE id = :id", {"id": user["id"]})
            except Exception as e:
                log("error", "Login", f"Failed to update last_login for user {username}: {str(e)}")
            
            log("info", "Login", f"User {username} logged in successfully")
            return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        log("error", "Login", f"Password verification error for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "System error. Please try again."},
        )

    # Failed login - increment attempts and potentially lock account
    new_attempts = user["failed_login_attempts"] + 1
    max_attempts = 5  # Lock after 5 failed attempts
    lock_duration_minutes = 15  # Lock for 15 minutes

    if new_attempts >= max_attempts:
        # Lock the account
        execute(
            "UPDATE users SET failed_login_attempts = :attempts, locked_until = NOW() + INTERVAL ':minutes minutes' WHERE id = :id",
            {"attempts": new_attempts, "minutes": lock_duration_minutes, "id": user["id"]}
        )
        log("warning", "Login", f"Account locked for user {username} after {new_attempts} failed attempts")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Account locked due to too many failed attempts. Try again in {lock_duration_minutes} minutes."},
        )
    else:
        # Just increment attempts
        execute("UPDATE users SET failed_login_attempts = :attempts WHERE id = :id", {"attempts": new_attempts, "id": user["id"]})
        remaining_attempts = max_attempts - new_attempts
        log("warning", "Login", f"Failed login attempt {new_attempts}/{max_attempts} for user: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": f"Invalid credentials. {remaining_attempts} attempts remaining before account lockout."},
        )

@router.get("/set-password")
def set_password_form(request: Request):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("set-password.html", {"request": request})

@router.post("/set-password")
def set_password(request: Request, password: str = Form(...), confirm_password: str = Form(...)):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")
    
    # Validate password matches confirmation
    if password != confirm_password:
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Passwords do not match"},
        )

    # Validate password strength (same as profile.py)
    if len(password) < 8:
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Password must be at least 8 characters long"},
        )

    if not re.search(r"[a-z]", password):
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Password must contain at least one lowercase letter"},
        )

    if not re.search(r"[A-Z]", password):
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Password must contain at least one uppercase letter"},
        )

    if not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Password must contain at least one number"},
        )

    try:
        hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        execute("UPDATE users SET password_hash = :h WHERE id = :id", {"h": hash_pw, "id": user_id})
        del request.session["needs_password"]
        
        log("info", "Login", f"User {username} successfully set their initial password")
        return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        log("error", "Login", f"Failed to set password for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "set-password.html",
            {"request": request, "error": "Failed to set password. Please try again."},
        )

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse("forgot-password.html", {"request": request})

@router.post("/forgot-password")
def forgot_password_submit(request: Request, email: str = Form(...)):
    if not email:
        return templates.TemplateResponse(
            "forgot-password.html",
            {"request": request, "error": "Email address is required"},
        )

    try:
        user = query("SELECT id, username, email, failed_login_attempts, locked_until FROM users WHERE email = :email AND enabled = TRUE", {"email": email}).mappings().first()
        
        if user:
            # Check if account is currently locked
            account_locked = user["locked_until"] and user["locked_until"] > query("SELECT NOW()").scalar()
            
            # Generate secure reset token
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = datetime.now() + timedelta(hours=1)  # Token expires in 1 hour
            
            # Store token in database
            execute(
                "UPDATE users SET password_reset_token = :token, password_reset_expires = :expires WHERE id = :id",
                {"token": token_hash, "expires": expires, "id": user["id"]}
            )
            
            # Send appropriate email based on account status
            reset_link = f"http://localhost:8000/reset-password?token={token}"
            
            if account_locked:
                email_body = f"""
Hello {user["username"]},

Your account has been temporarily locked due to too many failed login attempts.

Click the link below to unlock your account and reset your password:
{reset_link}

This link will expire in 1 hour.

If you did not request this unlock, please ignore this email.

Best regards,
Daygle Mail Archiver
"""
                email_subject = "Account Unlock - Daygle Mail Archiver"
            else:
                email_body = f"""
Hello {user["username"]},

You have requested to reset your password for Daygle Mail Archiver.

Click the link below to reset your password:
{reset_link}

This link will expire in 1 hour.

If you did not request this password reset, please ignore this email.

Best regards,
Daygle Mail Archiver
"""
                email_subject = "Password Reset - Daygle Mail Archiver"
            
            try:
                send_email(
                    to_email=user["email"],
                    subject=email_subject,
                    body=email_body
                )
                if account_locked:
                    log("info", "Account Unlock", f"Account unlock email sent to {user['email']} for user {user['username']}", "")
                else:
                    log("info", "Password Reset", f"Password reset email sent to {user['email']} for user {user['username']}", "")
            except Exception as e:
                log("error", "Password Reset", f"Failed to send email to {user['email']}: {str(e)}")
                return templates.TemplateResponse(
                    "forgot-password.html",
                    {"request": request, "error": "Failed to send reset email. Please try again."},
                )
        
        # Always show success message to prevent email enumeration
        return templates.TemplateResponse(
            "forgot-password.html",
            {"request": request, "success": "If an account with that email exists, a password reset/unlock link has been sent."},
        )
        
    except Exception as e:
        log("error", "Password Reset", f"Database error during password reset request for {email}: {str(e)}")
        return templates.TemplateResponse(
            "forgot-password.html",
            {"request": request, "error": "System error. Please try again."},
        )

@router.get("/reset-password")
def reset_password_form(request: Request, token: str = ""):
    if not token:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid reset token"},
        )
    
    # Verify token
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = query(
        "SELECT id, username FROM users WHERE password_reset_token = :token AND password_reset_expires > NOW()",
        {"token": token_hash}
    ).mappings().first()
    
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid or expired reset token"},
        )
    
    return templates.TemplateResponse("reset-password.html", {"request": request, "token": token})

@router.post("/reset-password")
def reset_password_submit(request: Request, token: str = Form(...), password: str = Form(...), confirm_password: str = Form(...)):
    if not token:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid reset token"},
        )
    
    # Verify token
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    user = query(
        "SELECT id, username FROM users WHERE password_reset_token = :token AND password_reset_expires > NOW()",
        {"token": token_hash}
    ).mappings().first()
    
    if not user:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid or expired reset token"},
        )
    
    # Validate passwords
    if password != confirm_password:
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Passwords do not match"},
        )

    # Validate password strength
    if len(password) < 8:
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Password must be at least 8 characters long"},
        )

    if not re.search(r"[a-z]", password):
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Password must contain at least one lowercase letter"},
        )

    if not re.search(r"[A-Z]", password):
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Password must contain at least one uppercase letter"},
        )

    if not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Password must contain at least one number"},
        )

    try:
        # Hash new password and clear reset token
        hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        execute(
            "UPDATE users SET password_hash = :password, password_reset_token = NULL, password_reset_expires = NULL, failed_login_attempts = 0, locked_until = NULL WHERE id = :id",
            {"password": hash_pw, "id": user["id"]}
        )
        
        log("info", "Password Reset", f"Password successfully reset for user {user['username']}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "success": "Password reset successfully. You can now log in with your new password."},
        )
        
    except Exception as e:
        log("error", "Password Reset", f"Failed to reset password for user {user['username']}: {str(e)}")
        return templates.TemplateResponse(
            "reset-password.html",
            {"request": request, "token": token, "error": "Failed to reset password. Please try again."},
        )
