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

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("profile.html", {"request": request, "flash": msg})

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