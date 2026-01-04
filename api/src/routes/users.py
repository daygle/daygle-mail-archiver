from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
import bcrypt

from utils.db import query
from utils.templates import templates
from utils.timezone import format_datetime

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/users")
def list_users(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    users = query("""
        SELECT id, username, first_name, last_name, email, role, enabled, last_login, created_at 
        FROM users 
        ORDER BY id
    """).mappings().all()
    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "flash": msg},
    )

@router.post("/users/create")
def create_user(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("administrator"),
    enabled: bool = Form(True)
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        query("""
            INSERT INTO users (username, password_hash, first_name, last_name, email, role, enabled) 
            VALUES (:u, :h, :fn, :ln, :e, :r, :en)
        """, {
            "u": username, 
            "h": hash_pw, 
            "fn": first_name,
            "ln": last_name,
            "e": email,
            "r": role,
            "en": enabled
        })
        flash(request, f"User {username} created successfully.")
    except Exception as e:
        flash(request, f"User creation failed: {str(e)}")
    return RedirectResponse("/users", status_code=303)

@router.get("/api/users/{user_id}")
def get_user(request: Request, user_id: int):
    """API endpoint to get user details for editing"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    user = query("""
        SELECT id, username, first_name, last_name, email, role, enabled, last_login, created_at 
        FROM users 
        WHERE id = :id
    """, {"id": user_id}).mappings().first()
    
    if not user:
        return JSONResponse({"error": "User not found"}, status_code=404)
    
    # Get current user's ID for timezone conversion
    current_user_id = request.session.get("user_id")
    
    return {
        "id": user["id"],
        "username": user["username"],
        "first_name": user["first_name"] or "",
        "last_name": user["last_name"] or "",
        "email": user["email"] or "",
        "role": user["role"] or "administrator",
        "enabled": user["enabled"],
        "last_login": format_datetime(user["last_login"], current_user_id) if user["last_login"] else None,
        "created_at": format_datetime(user["created_at"], current_user_id) if user["created_at"] else None
    }

@router.post("/users/{user_id}/update")
def update_user(
    request: Request,
    user_id: int,
    username: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("administrator"),
    enabled: bool = Form(False),
    password: str = Form("")
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    
    try:
        if password:
            # Update with new password
            hash_pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            query("""
                UPDATE users 
                SET username = :u, first_name = :fn, last_name = :ln, 
                    email = :e, role = :r, enabled = :en, password_hash = :h
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "r": role,
                "en": enabled if user_id != current_user_id else True,  # Don't disable own account
                "h": hash_pw,
                "id": user_id
            })
        else:
            # Update without password change
            query("""
                UPDATE users 
                SET username = :u, first_name = :fn, last_name = :ln, 
                    email = :e, role = :r, enabled = :en
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "r": role,
                "en": enabled if user_id != current_user_id else True,  # Don't disable own account
                "id": user_id
            })
        flash(request, "User updated successfully.")
    except Exception as e:
        flash(request, f"User update failed: {str(e)}")
    
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

@router.post("/users/{user_id}/toggle")
def toggle_user_enabled(request: Request, user_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    if user_id == current_user_id:
        flash(request, "Cannot disable your own account.")
        return RedirectResponse("/users", status_code=303)

    try:
        # Toggle the enabled status
        query(
            "UPDATE users SET enabled = NOT enabled WHERE id = :id",
            {"id": user_id}
        )
        flash(request, "User status updated successfully.")
    except Exception as e:
        flash(request, f"User status update failed: {str(e)}")
    return RedirectResponse("/users", status_code=303)