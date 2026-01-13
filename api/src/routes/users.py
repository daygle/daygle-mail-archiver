from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
import bcrypt
import re
from typing import List

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates
from utils.timezone import format_datetime
from utils.permissions import require_permission, PERMISSIONS

router = APIRouter()


def flash(request: Request, message, category: str = "info"):
    request.session["flash"] = (
        message if isinstance(message, dict) else {"message": message, "type": category}
    )


# ---------------------------------------------------------
# LIST USERS
# ---------------------------------------------------------
@router.get("/users")
def list_users(
    request: Request,
    _=require_permission(PERMISSIONS["view_users"])
):
    users = query("""
        SELECT 
            u.id, u.username, u.first_name, u.last_name, u.email,
            COALESCE(u.email_notifications, TRUE) AS email_notifications,
            u.enabled, u.last_login, u.last_login_ip, u.created_at,
            STRING_AGG(r.display_name, ', ') AS roles
        FROM users u
        LEFT JOIN user_roles ur ON u.id = ur.user_id
        LEFT JOIN roles r ON ur.role_id = r.id
        GROUP BY u.id
        ORDER BY u.id
    """).mappings().all()

    roles = query("""
        SELECT id, name, display_name, description
        FROM roles
        ORDER BY COALESCE(display_name, name)
    """).mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": users, "roles": roles, "flash": msg},
    )


# ---------------------------------------------------------
# CREATE USER
# ---------------------------------------------------------
@router.post("/users/create")
def create_user(
    request: Request,
    _=require_permission(PERMISSIONS["manage_users"]),
    username: str = Form(...),
    password: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role_ids: List[str] = Form([]),
    email_notifications: bool = Form(True),
    enabled: bool = Form(True)
):
    admin_username = request.session.get("username", "unknown")

    # Normalize input
    username = username.strip()
    first_name = first_name.strip() or None
    last_name = last_name.strip() or None
    email = email.strip() or None

    # Username validation
    if len(username) < 3:
        flash(request, "Username must be at least 3 characters long.", "error")
        return RedirectResponse("/users", status_code=303)

    # Unique username
    if query("SELECT id FROM users WHERE username = :u", {"u": username}).first():
        flash(request, f"Username '{username}' already exists.", "error")
        return RedirectResponse("/users", status_code=303)

    # Password validation
    if (
        len(password) < 8
        or not re.search(r"[a-z]", password)
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[0-9]", password)
    ):
        flash(request, "Password must include upper, lower, number and be 8+ chars.", "error")
        return RedirectResponse("/users", status_code=303)

    # Email validation
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.", "error")
        return RedirectResponse("/users", status_code=303)

    # Must have at least one role
    if not role_ids:
        flash(request, "At least one role must be assigned.", "error")
        return RedirectResponse("/users", status_code=303)

    try:
        # Hash password
        hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Create user
        new_user = query("""
            INSERT INTO users (username, password_hash, first_name, last_name, email, email_notifications, enabled)
            VALUES (:u, :h, :fn, :ln, :e, :enf, :en)
            RETURNING id
        """, {
            "u": username,
            "h": hash_pw,
            "fn": first_name,
            "ln": last_name,
            "e": email,
            "enf": email_notifications,
            "en": enabled
        }).mappings().first()

        user_id = new_user["id"]

        # Assign roles
        for role_id in role_ids:
            execute("""
                INSERT INTO user_roles (user_id, role_id)
                VALUES (:user_id, :role_id)
            """, {"user_id": user_id, "role_id": int(role_id)})

        log("info", "Users", f"Admin '{admin_username}' created user '{username}' with roles {role_ids}")
        flash(request, f"User '{username}' created successfully", "success")

    except Exception as e:
        log("error", "Users", f"Failed to create user '{username}': {str(e)}")
        flash(request, "User creation failed. Please try again.", "error")

    return RedirectResponse("/users", status_code=303)

@router.get("/api/users/{user_id}")
def get_user(
    request: Request,
    user_id: int,
    _=require_permission(PERMISSIONS["view_users"])
):
    """API endpoint to get user details for editing"""

    try:
        user = query("""
            SELECT id, username, first_name, last_name, email,
                   COALESCE(email_notifications, TRUE) AS email_notifications,
                   enabled, last_login, last_login_ip, created_at
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)

        # Current user's ID for timezone conversion
        current_user_id = int(request.session.get("user_id"))

        # Get assigned role IDs
        role_rows = query("""
            SELECT role_id
            FROM user_roles
            WHERE user_id = :id
        """, {"id": user_id}).mappings().all()

        role_ids = [r["role_id"] for r in role_rows]

        return {
            "id": user["id"],
            "username": user["username"],
            "first_name": user["first_name"] or "",
            "last_name": user["last_name"] or "",
            "email": user["email"] or "",
            "role_ids": role_ids,
            "email_notifications": user["email_notifications"],
            "enabled": user["enabled"],
            "last_login": format_datetime(user["last_login"], current_user_id)
                if user["last_login"] else None,
            "last_login_ip": user["last_login_ip"] or None,
            "created_at": format_datetime(user["created_at"], current_user_id)
                if user["created_at"] else None
        }

    except Exception as e:
        admin_username = request.session.get("username", "unknown")
        log("error", "Users",
            f"Failed to fetch user {user_id} for admin '{admin_username}': {str(e)}")
        return JSONResponse({"error": "Failed to load user data"}, status_code=500)

# ---------------------------------------------------------
# UPDATE USER
# ---------------------------------------------------------
@router.post("/users/{user_id}/update")
def update_user(
    request: Request,
    user_id: int,
    _=require_permission(PERMISSIONS["manage_users"]),
    username: str = Form(...),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    role_ids: List[str] = Form([]),
    email_notifications: bool = Form(True),
    enabled: bool = Form(False),
    password: str = Form("")
):
    admin_username = request.session.get("username", "unknown")
    current_user_id = int(request.session.get("user_id"))

    # Normalize input
    username = username.strip()
    first_name = first_name.strip() or None
    last_name = last_name.strip() or None
    email = email.strip() or None

    # Validate username
    if len(username) < 3:
        flash(request, "Username must be at least 3 characters long.", "error")
        return RedirectResponse("/users", status_code=303)

    # Unique username check
    existing = query("""
        SELECT id FROM users
        WHERE username = :u AND id != :id
    """, {"u": username, "id": user_id}).mappings().first()

    if existing:
        flash(request, f"Username '{username}' already exists.", "error")
        return RedirectResponse("/users", status_code=303)

    # Email validation
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, "Invalid email format.", "error")
        return RedirectResponse("/users", status_code=303)

    # Must have at least one role
    if not role_ids:
        flash(request, "At least one role must be assigned.", "error")
        return RedirectResponse(f"/users/{user_id}/edit", status_code=303)

    try:
        # Fetch current user values
        current = query("""
            SELECT username, first_name, last_name, email,
                   email_notifications, enabled
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        current_role_rows = query("""
            SELECT role_id FROM user_roles WHERE user_id = :id
        """, {"id": user_id}).mappings().all()

        # Prevent self‑lockout: user cannot remove their own admin-level access
        if user_id == current_user_id:
            # Check if the new role set still includes a role with manage_users permission
            admin_roles = query("""
                SELECT rp.role_id
                FROM role_permissions rp
                JOIN permissions p ON p.id = rp.permission_id
                WHERE p.name = 'manage_users'
            """).mappings().all()

            admin_role_ids = {str(r["role_id"]) for r in admin_roles}

            if not (new_role_ids & admin_role_ids):
                flash(request, "You cannot remove your own administrative access.", "error")
                return RedirectResponse("/users", status_code=303)

        current_role_ids = {str(r["role_id"]) for r in current_role_rows}
        new_role_ids = {str(r) for r in role_ids}

        # Detect no-op
        if (
            not password
            and current
            and current["username"] == username
            and (current["first_name"] or None) == first_name
            and (current["last_name"] or None) == last_name
            and (current["email"] or None) == email
            and bool(current["email_notifications"]) == bool(email_notifications)
            and bool(current["enabled"]) == (True if user_id == current_user_id else bool(enabled))
            and current_role_ids == new_role_ids
        ):
            flash(request, "No changes detected.", "info")
            return RedirectResponse("/users", status_code=303)

        # Password update
        if password:
            if (
                len(password) < 8
                or not re.search(r"[a-z]", password)
                or not re.search(r"[A-Z]", password)
                or not re.search(r"[0-9]", password)
            ):
                flash(request, "Password must include upper, lower, number and be 8+ chars.", "error")
                return RedirectResponse("/users", status_code=303)

            hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

            execute("""
                UPDATE users
                SET username = :u, first_name = :fn, last_name = :ln,
                    email = :e, email_notifications = :enf,
                    enabled = :en, password_hash = :h
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "enf": email_notifications,
                "en": True if user_id == current_user_id else enabled,
                "h": hash_pw,
                "id": user_id
            })

            log("warning", "Users",
                f"Admin '{admin_username}' updated user '{username}' (ID: {user_id}) including password reset")

        else:
            # Update without password
            execute("""
                UPDATE users
                SET username = :u, first_name = :fn, last_name = :ln,
                    email = :e, email_notifications = :enf,
                    enabled = :en
                WHERE id = :id
            """, {
                "u": username,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "enf": email_notifications,
                "en": True if user_id == current_user_id else enabled,
                "id": user_id
            })

            log("info", "Users",
                f"Admin '{admin_username}' updated user '{username}' (ID: {user_id})")

        # Update role assignments
        execute("DELETE FROM user_roles WHERE user_id = :id", {"id": user_id})
        for rid in new_role_ids:
            execute("""
                INSERT INTO user_roles (user_id, role_id)
                VALUES (:user_id, :role_id)
            """, {"user_id": user_id, "role_id": int(rid)})

        flash(request, "User updated successfully.", "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to update user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, "User update failed. Please try again.", "error")

    return RedirectResponse("/users", status_code=303)


# ---------------------------------------------------------
# DELETE USER
# ---------------------------------------------------------
@router.post("/users/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    _=require_permission(PERMISSIONS["manage_users"])
):
    current_user_id = int(request.session.get("user_id"))
    admin_username = request.session.get("username", "unknown")

    if user_id == current_user_id:
        flash(request, "You cannot delete your own account.", "error")
        return RedirectResponse("/users", status_code=303)

    try:
        user = query("""
            SELECT username FROM users WHERE id = :id
        """, {"id": user_id}).mappings().first()

        username = user["username"] if user else f"ID {user_id}"

        execute("DELETE FROM users WHERE id = :id", {"id": user_id})

        log("info", "Users",
            f"Admin '{admin_username}' deleted user '{username}' (ID: {user_id})")

        flash(request, "User deleted successfully.", "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to delete user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, "User deletion failed. Please try again.", "error")

    return RedirectResponse("/users", status_code=303)


# ---------------------------------------------------------
# TOGGLE USER ENABLED
# ---------------------------------------------------------
@router.post("/users/{user_id}/toggle")
def toggle_user_enabled(
    request: Request,
    user_id: int,
    _=require_permission(PERMISSIONS["manage_users"])
):
    current_user_id = int(request.session.get("user_id"))
    admin_username = request.session.get("username", "unknown")

    if user_id == current_user_id:
        flash(request, "You cannot disable your own account.", "error")
        return RedirectResponse("/users", status_code=303)

    try:
        user = query("""
            SELECT username, enabled
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        if not user:
            flash(request, "User not found.", "error")
            return RedirectResponse("/users", status_code=303)

        execute("""
            UPDATE users
            SET enabled = NOT enabled
            WHERE id = :id
        """, {"id": user_id})

        new_status = "disabled" if user["enabled"] else "enabled"

        log("info", "Users",
            f"Admin '{admin_username}' {new_status} user '{user['username']}' (ID: {user_id})")

        flash(request, "User status updated successfully.", "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to toggle user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, "User status update failed. Please try again.", "error")

    return RedirectResponse("/users", status_code=303)