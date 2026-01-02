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

@router.get("/users")
def list_users(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    users = query("SELECT id, username, created_at FROM users ORDER BY id").mappings().all()
    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "flash": msg},
    )

@router.post("/users/create")
def create_user(request: Request, username: str = Form(...), password: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        query("INSERT INTO users (username, password_hash) VALUES (:u, :h)", {"u": username, "h": hash_pw})
        flash(request, f"User {username} created successfully.")
    except Exception as e:
        flash(request, f"User creation failed: {str(e)}")
    return RedirectResponse("/users", status_code=303)

@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    if user_id == current_user_id:
        flash(request, "Cannot delete your own account.")
        return RedirectResponse("/users", status_code=303)

    try:
        query("DELETE FROM users WHERE id = :id", {"id": user_id})
        flash(request, "User deleted successfully.")
    except Exception as e:
        flash(request, f"User deletion failed: {str(e)}")
    return RedirectResponse("/users", status_code=303)