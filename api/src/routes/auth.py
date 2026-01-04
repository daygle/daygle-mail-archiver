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

@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    try:
        user = query(
            "SELECT id, username, password_hash, date_format, timezone, role, enabled FROM users WHERE username = :u",
            {"u": username}
        ).mappings().first()
    except Exception as e:
        log("error", "auth", f"Database error during login for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "System error. Please try again."},
        )

    if not user:
        log("warning", "auth", f"Failed login attempt for unknown user: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials"},
        )

    if not user["enabled"]:
        log("warning", "auth", f"Failed login attempt for disabled user: {username}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "This account has been disabled"},
        )

    if not user["password_hash"]:
        # First login, set password
        request.session["user_id"] = user["id"]
        request.session["username"] = user["username"]
        request.session["date_format"] = user["date_format"] or "%d/%m/%Y %H:%M"
        request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
        request.session["role"] = user["role"] or "administrator"
        request.session["needs_password"] = True
        log("info", "auth", f"User {username} initiated first login")
        return RedirectResponse("/set_password", status_code=303)

    try:
        if bcrypt.checkpw(password.encode('utf-8'), user["password_hash"].encode('utf-8')):
            request.session["user_id"] = user["id"]
            request.session["username"] = user["username"]
            request.session["date_format"] = user["date_format"] or "%d/%m/%Y %H:%M"
            request.session["timezone"] = user["timezone"] or "Australia/Melbourne"
            request.session["role"] = user["role"] or "administrator"
            
            # Update last_login timestamp
            try:
                execute("UPDATE users SET last_login = NOW() WHERE id = :id", {"id": user["id"]})
            except Exception as e:
                log("error", "auth", f"Failed to update last_login for user {username}: {str(e)}")
            
            log("info", "auth", f"User {username} logged in successfully")
            return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        log("error", "auth", f"Password verification error for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "System error. Please try again."},
        )

    log("warning", "auth", f"Failed login attempt for user: {username}")
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials"},
    )

@router.get("/set_password")
def set_password_form(request: Request):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse("set_password.html", {"request": request})

@router.post("/set_password")
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
        
        log("info", "auth", f"User {username} successfully set their initial password")
        return RedirectResponse("/dashboard", status_code=303)
    except Exception as e:
        log("error", "auth", f"Failed to set password for user {username}: {str(e)}")
        return templates.TemplateResponse(
            "set_password.html",
            {"request": request, "error": "Failed to set password. Please try again."},
        )

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)