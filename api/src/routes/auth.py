from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt

from utils.db import query
from utils.logger import log

templates = Jinja2Templates(directory="templates")

router = APIRouter()

def logged_in(request: Request):
    return "user_id" in request.session

@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = query(
        "SELECT id, username, password_hash, date_format, enabled FROM users WHERE username = :u",
        {"u": username}
    ).mappings().first()

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
        request.session["date_format"] = user["date_format"]
        request.session["needs_password"] = True
        log("info", "auth", f"User {username} initiated first login")
        return RedirectResponse("/set_password", status_code=303)

    if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        request.session["user_id"] = user["id"]
        request.session["username"] = user["username"]
        request.session["date_format"] = user["date_format"]
        
        # Update last_login timestamp
        query("UPDATE users SET last_login = NOW() WHERE id = :id", {"id": user["id"]})
        
        log("info", "auth", f"User {username} logged in successfully")
        return RedirectResponse("/dashboard", status_code=303)

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
def set_password(request: Request, password: str = Form(...)):
    if not request.session.get("needs_password"):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    query("UPDATE users SET password_hash = :h WHERE id = :id", {"h": hash_pw, "id": user_id})
    del request.session["needs_password"]
    return RedirectResponse("/dashboard", status_code=303)

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)