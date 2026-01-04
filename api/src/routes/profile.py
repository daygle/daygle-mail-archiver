from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str):
    request.session["flash"] = message

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

@router.post("/profile/change_password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...)
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    user = query("SELECT password_hash FROM users WHERE id = :id", {"id": user_id}).mappings().first()

    if not user or not bcrypt.checkpw(current_password.encode(), user["password_hash"].encode()):
        flash(request, "Current password is incorrect.")
        return RedirectResponse("/profile", status_code=303)

    if new_password != confirm_password:
        flash(request, "New passwords do not match.")
        return RedirectResponse("/profile", status_code=303)

    if len(new_password) < 6:
        flash(request, "New password must be at least 6 characters long.")
        return RedirectResponse("/profile", status_code=303)

    hash_pw = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    query("UPDATE users SET password_hash = :h WHERE id = :id", {"h": hash_pw, "id": user_id})
    flash(request, "Password changed successfully.")
    return RedirectResponse("/profile", status_code=303)

@router.post("/profile/update_info")
def update_info(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form("")
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    query("""
        UPDATE users 
        SET first_name = :fn, last_name = :ln, email = :e 
        WHERE id = :id
    """, {"fn": first_name, "ln": last_name, "e": email, "id": user_id})
    
    flash(request, "Profile updated successfully.")
    return RedirectResponse("/profile", status_code=303)

@router.get("/user_settings")
def user_settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    user = query("SELECT date_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
    current_format = user["date_format"] if user else "%d/%m/%Y %H:%M"

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("user_settings.html", {
        "request": request, 
        "flash": msg, 
        "date_format": current_format
    })

@router.post("/user_settings/change_date_format")
def change_date_format(request: Request, date_format: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    user_id = request.session["user_id"]
    query("UPDATE users SET date_format = :f WHERE id = :id", {"f": date_format, "id": user_id})
    request.session["date_format"] = date_format
    flash(request, "Date format updated successfully.")
    return RedirectResponse("/user_settings", status_code=303)