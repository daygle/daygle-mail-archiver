from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
import bcrypt

from utils.db import query

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
        "SELECT id, username, password_hash FROM users WHERE username = :u",
        {"u": username}
    ).mappings().first()

    if user and bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        request.session["user_id"] = user["id"]
        request.session["username"] = user["username"]
        return RedirectResponse("/messages", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid credentials"},
    )

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)