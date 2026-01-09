from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
import bcrypt
import re
from typing import List

from utils.db import query, execute
from utils.logger import log
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

    # Get users with their assigned roles (use display_name for UI)
    users = query("""
        SELECT u.id, u.username, u.first_name, u.last_name, u.email,
               COALESCE(u.email_notifications, TRUE) as email_notifications,
               u.enabled, u.last_login, u.created_at,
               COALESCE(STRING_AGG(r.display_name, ', '), '') as roles
        FROM users u
        LEFT JOIN user_roles ur ON u.id = ur.user_id
        LEFT JOIN roles r ON ur.role_id = r.id
        GROUP BY u.id, u.username, u.first_name, u.last_name, u.email,
                 u.email_notifications, u.enabled, u.last_login, u.created_at
        ORDER BY u.id
    """).mappings().all()

    # Get all available roles for the form
    roles = query("SELECT id, name, description FROM roles ORDER BY name").mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "roles": roles, "flash": msg},
    )

@router.post("/users/create")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role_ids: List[str] = Form([]),
    email_notifications: bool = Form(True),
    enabled: bool = Form(True)
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    admin_username = request.session.get("username", "unknown")
    
    # Sanitize inputs
    username = username.strip()
    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip()
    
    # Validate username
    if not username or len(username) < 3:
        flash(request, "Username must be at least 3 characters long.")
        return RedirectResponse("/users", status_code=303)
    
    # Check username uniqueness
    existing = query("SELECT id FROM users WHERE username = :u", {"u": username}).mappings().first()
    if existing:
        flash(request, f"Username '{username}' already exists.")
        return RedirectResponse("/users", status_code=303)
    
    # Validate password strength
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters long.")
        return RedirectResponse("/users", status_code=303)
    
    if not re.search(r"[a-z]", password):
        flash(request, "Password must contain at least one lowercase letter.")
        return RedirectResponse("/users", status_code=303)
    
    if not re.search(r"[A-Z]", password):
        flash(request, "Password must contain at least one uppercase letter.")
        return RedirectResponse("/users", status_code=303)
    
    if not re.search(r"[0-9]", password):
        flash(request, "Password must contain at least one number.")
        return RedirectResponse("/users", status_code=303)
    
    # Validate email format if provided
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.")
        return RedirectResponse("/users", status_code=303)
    
    # Validate that at least one role is selected
    if not role_ids:
        flash(request, "At least one role must be assigned to the user.")
        return RedirectResponse("/users", status_code=303)
    
    try:
        hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        user_id = execute("""
            INSERT INTO users (username, password_hash, first_name, last_name, email, email_notifications, enabled)
            VALUES (:u, :h, :fn, :ln, :e, :enf, :en)
        """, {
            "u": username,
            "h": hash_pw,
            "fn": first_name or None,
            "ln": last_name or None,
            "e": email or None,
            "enf": email_notifications,
            "en": enabled
        })

        # Assign roles to the user
        for role_id in role_ids:
            try:
                execute("""
                    INSERT INTO user_roles (user_id, role_id)
                    VALUES (:user_id, :role_id)
                """, {"user_id": user_id, "role_id": int(role_id)})
            except Exception as e:
                log("error", "Users", f"Failed to assign role {role_id} to user {username}: {str(e)}")

        log("info", "Users", f"Admin '{admin_username}' created new user '{username}' with {len(role_ids)} roles")
        flash(request, f"User '{username}' created successfully")
        return RedirectResponse("/users", status_code=303)
    except Exception as e:
        log("error", "Users", f"Failed to create user '{username}' by admin '{admin_username}': {str(e)}", "")
        flash(request, "User creation failed. Please try again.")
    return RedirectResponse("/users", status_code=303)

@router.get("/api/users/{user_id}")
def get_user(request: Request, user_id: int):
    """API endpoint to get user details for editing"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        user = query("""
            SELECT id, username, first_name, last_name, email, role, 
                   COALESCE(email_notifications, TRUE) as email_notifications,
                   enabled, last_login, created_at 
            FROM users 
            WHERE id = :id
        """, {"id": user_id}).mappings().first()
        
        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)
        
        # Get current user's ID for timezone conversion
        current_user_id = int(request.session.get("user_id"))
        
        return {
            "id": user["id"],
            "username": user["username"],
            "first_name": user["first_name"] or "",
            "last_name": user["last_name"] or "",
            "email": user["email"] or "",
            "role": user["role"] or "administrator",
            "email_notifications": user["email_notifications"],
            "enabled": user["enabled"],
            "last_login": format_datetime(user["last_login"], current_user_id) if user["last_login"] else None,
            "created_at": format_datetime(user["created_at"], current_user_id) if user["created_at"] else None
        }
    except Exception as e:
        admin_username = request.session.get("username", "unknown")
        log("error", "Users", f"Failed to fetch user {user_id} for admin '{admin_username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load user data"}, status_code=500)

@router.post("/users/{user_id}/update")
def update_user(
    request: Request,
    user_id: int,
    username: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role: str = Form("administrator"),
    email_notifications: bool = Form(True),
    enabled: bool = Form(False),
    password: str = Form("")
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    admin_username = request.session.get("username", "unknown")
    
    # Sanitize inputs
    username = username.strip()
    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip()
    
    # Validate username
    if not username or len(username) < 3:
        flash(request, "Username must be at least 3 characters long.")
        return RedirectResponse("/users", status_code=303)
    
    # Check username uniqueness (excluding current user)
    existing = query(
        "SELECT id FROM users WHERE username = :u AND id != :id",
        {"u": username, "id": user_id}
    ).mappings().first()
    if existing:
        flash(request, f"Username '{username}' already exists.")
        return RedirectResponse("/users", status_code=303)
    
    # Validate email format if provided
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.")
        return RedirectResponse("/users", status_code=303)
    
    try:
        if password:
            # Validate password strength if changing
            if len(password) < 8:
                flash(request, "Password must be at least 8 characters long.")
                return RedirectResponse("/users", status_code=303)
            
            if not re.search(r"[a-z]", password):
                flash(request, "Password must contain at least one lowercase letter.")
                return RedirectResponse("/users", status_code=303)
            
            if not re.search(r"[A-Z]", password):
                flash(request, "Password must contain at least one uppercase letter.")
                return RedirectResponse("/users", status_code=303)
            
            if not re.search(r"[0-9]", password):
                flash(request, "Password must contain at least one number.")
                return RedirectResponse("/users", status_code=303)
            
            # Update with new password
            hash_pw = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            execute("""
                UPDATE users 
                SET username = :u, first_name = :fn, last_name = :ln, 
                    email = :e, role = :r, email_notifications = :enf, enabled = :en, password_hash = :h
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "r": role,
                "enf": email_notifications,
                "en": enabled if user_id != current_user_id else True,
                "h": hash_pw,
                "id": user_id
            })
            log("warning", "Users", f"Admin '{admin_username}' updated user '{username}' (ID: {user_id}) including password reset", "")
        else:
            # Update without password change
            execute("""
                UPDATE users 
                SET username = :u, first_name = :fn, last_name = :ln, 
                    email = :e, role = :r, email_notifications = :enf, enabled = :en
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "r": role,
                "enf": email_notifications,
                "en": enabled if user_id != current_user_id else True,
                "id": user_id
            })
            log("info", "Users", f"Admin '{admin_username}' updated user '{username}' (ID: {user_id})", "")
        flash(request, "User updated successfully.")
    except Exception as e:
        log("error", "Users", f"Failed to update user {user_id} by admin '{admin_username}': {str(e)}", "")
        flash(request, "User update failed. Please try again.")
    
    return RedirectResponse("/users", status_code=303)

@router.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    admin_username = request.session.get("username", "unknown")
    
    if user_id == current_user_id:
        flash(request, "Cannot delete your own account.")
        return RedirectResponse("/users", status_code=303)

    try:
        # Get username before deletion for logging
        user = query("SELECT username FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        username = user["username"] if user else f"ID {user_id}"

        execute("DELETE FROM users WHERE id = :id", {"id": user_id})
        log("info", "Users", f"Admin '{admin_username}' deleted user '{username}' (ID: {user_id})", "")
        flash(request, "User deleted successfully.")
    except Exception as e:
        error_msg = str(e).lower()
        if "foreign key" in error_msg:
            flash(request, "Cannot delete user due to existing references. Please reassign or clear their data first.")
        else:
            flash(request, "User deletion failed. Please try again.")
        log("error", "Users", f"Failed to delete user {user_id} by admin '{admin_username}': {str(e)}", "")
    return RedirectResponse("/users", status_code=303)

@router.post("/users/{user_id}/toggle")
def toggle_user_enabled(request: Request, user_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    current_user_id = request.session.get("user_id")
    admin_username = request.session.get("username", "unknown")
    
    if user_id == current_user_id:
        flash(request, "Cannot disable your own account.")
        return RedirectResponse("/users", status_code=303)

    try:
        # Get username for logging
        user = query("SELECT username, enabled FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if not user:
            flash(request, "User not found.")
            return RedirectResponse("/users", status_code=303)
        
        # Toggle the enabled status
        execute(
            "UPDATE users SET enabled = NOT enabled WHERE id = :id",
            {"id": user_id}
        )
        new_status = "disabled" if user["enabled"] else "enabled"
        log("info", "Users", f"Admin '{admin_username}' {new_status} user '{user['username']}' (ID: {user_id})", "")
        flash(request, "User status updated successfully.")
    except Exception as e:
        log("error", "Users", f"Failed to toggle user {user_id} by admin '{admin_username}': {str(e)}", "")
        flash(request, "User status update failed. Please try again.")
    return RedirectResponse("/users", status_code=303)