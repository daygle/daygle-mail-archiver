from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
import bcrypt
import re

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates

router = APIRouter()

def logged_in(request: Request):
    return "user_id" in request.session

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
    
    return templates.TemplateResponse("setup_wizard.html", {"request": request})

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
            "setup_wizard.html",
            {"request": request, "error": "Username must be at least 3 characters long", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Check if username already exists
    try:
        existing_user = query("SELECT id FROM users WHERE username = :u", {"u": username}).mappings().first()
        if existing_user:
            return templates.TemplateResponse(
                "setup_wizard.html",
                {"request": request, "error": "Username already exists", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
            )
    except Exception as e:
        log("error", "Setup", f"Database error checking username: {str(e)}")
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "System error. Please try again.", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Validate password matches confirmation
    if password != confirm_password:
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "Passwords do not match", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Validate password strength
    if len(password) < 8:
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "Password must be at least 8 characters long", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[a-z]", password):
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "Password must contain at least one lowercase letter", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[A-Z]", password):
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "Password must contain at least one uppercase letter", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    if not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
            "setup_wizard.html",
            {"request": request, "error": "Password must contain at least one number", "username": username, "first_name": first_name, "last_name": last_name, "email": email},
        )
    
    # Create the administrator account
    try:
        hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        execute(
            "INSERT INTO users (username, password_hash, first_name, last_name, email, role) VALUES (:username, :password_hash, :first_name, :last_name, :email, 'administrator')",
            {
                "username": username, 
                "password_hash": hash_pw, 
                "first_name": first_name if first_name else None,
                "last_name": last_name if last_name else None,
                "email": email if email else None
            }
        )
        
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
            "setup_wizard.html",
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
            "SELECT id, username, password_hash, date_format, time_format, timezone, role, enabled FROM users WHERE username = :u",
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
        request.session["role"] = user["role"] or "administrator"
        request.session["needs_password"] = True
        log("info", "Login", f"User {username} initiated first login")
        return RedirectResponse("/set-password", status_code=303)

    try:
        if bcrypt.checkpw(password.encode('utf-8'), user["password_hash"].encode('utf-8')):
            request.session["user_id"] = user["id"]
            request.session["username"] = user["username"]
            request.session["date_format"] = user["date_format"] or "%d/%m/%Y"
            request.session["time_format"] = user["time_format"] or "%H:%M"
            request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
            request.session["role"] = user["role"] or "administrator"
            
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

    log("warning", "Login", f"Failed login attempt for user: {username}")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials"},
    )

@router.get("/set-password")
def set_password_form(request: Request):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("set_password.html", {"request": request})

@router.post("/set-password")
def set_password(request: Request, password: str = Form(...), confirm_password: str = Form(...)):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    username = request.session.get("username", "unknown")
    
    # Validate password matches confirmation
    if password != confirm_password:
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "error": "Passwords do not match"},
        )
    
    # Validate password strength (same as profile.py)
    if len(password) < 8:
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "error": "Password must be at least 8 characters long"},
        )
    
    if not re.search(r"[a-z]", password):
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "error": "Password must contain at least one lowercase letter"},
        )
    
    if not re.search(r"[A-Z]", password):
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "error": "Password must contain at least one uppercase letter"},
        )
    
    if not re.search(r"[0-9]", password):
        return templates.TemplateResponse(
            "set_password.html",
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
            "set_password.html",
            {"request": request, "error": "Failed to set password. Please try again."},
        )

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)