from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import text
import bcrypt
import re
from typing import List

from ..utils.db import query, execute, transaction
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.timezone import format_datetime, get_display_prefs
from ..utils.permissions import require_permission, PERMISSIONS, check_privileged_role_assignment
from ..utils.i18n import request_gettext

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
    # Get auto-logout setting for online/offline calculation
    auto_logout_setting = query("SELECT value FROM settings WHERE key = 'auto_logout_minutes'").mappings().first()
    auto_logout_minutes = int(auto_logout_setting["value"]) if auto_logout_setting else 60

    users = query("""
        SELECT 
            u.id, u.username, u.first_name, u.last_name, u.email,
            COALESCE(u.email_notifications, TRUE) AS email_notifications,
            u.enabled, u.last_login, u.last_login_ip, u.created_at, u.last_seen,
            STRING_AGG(r.display_name, ', ') AS roles,
            CASE 
                WHEN u.last_seen IS NULL THEN 'offline'
                WHEN NOW() - u.last_seen > (INTERVAL '1 minute' * :minutes) THEN 'offline'
                ELSE 'online'
            END AS online_status
        FROM users u
        LEFT JOIN user_roles ur ON u.id = ur.user_id
        LEFT JOIN roles r ON ur.role_id = r.id
        GROUP BY u.id
        ORDER BY u.id
    """, {"minutes": auto_logout_minutes}).mappings().all()

    # Resolve display preferences once so the template never queries the DB
    # per row, then pre-format the timestamps.
    current_user_id = request.session.get("user_id")
    tz, date_format, time_format = get_display_prefs(current_user_id)

    display_users = []
    for u in users:
        row = dict(u)
        row["created_at_formatted"] = (
            format_datetime(u["created_at"], current_user_id, tz=tz, date_format=date_format, time_format=time_format)
            if u["created_at"] else None
        )
        row["last_login_formatted"] = (
            format_datetime(u["last_login"], current_user_id, tz=tz, date_format=date_format, time_format=time_format)
            if u["last_login"] else None
        )
        display_users.append(row)

    roles = query("""
        SELECT id, name, display_name, description
        FROM roles
        ORDER BY COALESCE(display_name, name)
    """).mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "users.html",
        {"request": request, "users": display_users, "roles": roles, "flash": msg},
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
    _ = request_gettext(request)
    admin_username = request.session.get("username", "unknown")

    # Normalize input
    username = username.strip()
    first_name = first_name.strip() or None
    last_name = last_name.strip() or None
    email = email.strip() or None

    # Username validation
    if len(username) < 3:
        flash(request, _("Username must be at least 3 characters long."), "error")
        return RedirectResponse("/users", status_code=303)

    # Unique username
    if query("SELECT id FROM users WHERE username = :u", {"u": username}).first():
        flash(request, _("Username '{0}' already exists.").format(username), "error")
        return RedirectResponse("/users", status_code=303)

    # Password validation
    if (
        len(password) < 8
        or not re.search(r"[a-z]", password)
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[0-9]", password)
    ):
        flash(request, _("Password must include upper, lower, number and be 8+ chars."), "error")
        return RedirectResponse("/users", status_code=303)

    # Email validation
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, _("Invalid email format."), "error")
        return RedirectResponse("/users", status_code=303)

    # Must have at least one role
    if not role_ids:
        flash(request, _("At least one role must be assigned."), "error")
        return RedirectResponse("/users", status_code=303)

    # Normalize + dedupe role ids before touching the DB
    try:
        role_ids = sorted({int(r) for r in role_ids})
    except (TypeError, ValueError):
        flash(request, _("Invalid role selection."), "error")
        return RedirectResponse("/users", status_code=303)

    # A user manager cannot assign roles that carry privileged permissions they
    # do not hold themselves (prevents self-escalation via a god-role).
    error = check_privileged_role_assignment(request, role_ids)
    if error:
        flash(request, error, "error")
        return RedirectResponse("/users", status_code=303)

    try:
        # Hash password
        hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # Create the user and assign roles atomically so a failure cannot leave
        # a user account with no (or a partial set of) roles.
        assigned_by = request.session.get("user_id")
        assigned_by = int(assigned_by) if assigned_by else None

        with transaction() as conn:
            new_user = conn.execute(text("""
                INSERT INTO users (username, password_hash, first_name, last_name, email, email_notifications, enabled)
                VALUES (:u, :h, :fn, :ln, :e, :enf, :en)
                RETURNING id
            """), {
                "u": username,
                "h": hash_pw,
                "fn": first_name,
                "ln": last_name,
                "e": email,
                "enf": email_notifications,
                "en": enabled
            }).mappings().first()

            if not new_user or "id" not in new_user:
                raise RuntimeError("INSERT INTO users did not return an id")

            user_id = int(new_user["id"])

            for role_id in role_ids:
                conn.execute(text("""
                    INSERT INTO user_roles (user_id, role_id, assigned_by)
                    VALUES (:user_id, :role_id, :assigned_by)
                """), {"user_id": user_id, "role_id": role_id, "assigned_by": assigned_by})

        log("info", "Users", f"Admin '{admin_username}' created user '{username}' with roles {role_ids}")
        flash(request, _("User '{0}' created successfully").format(username), "success")

    except Exception as e:
        log("error", "Users", f"Failed to create user '{username}': {str(e)}")
        flash(request, _("User creation failed. Please try again."), "error")

    return RedirectResponse("/users", status_code=303)

@router.get("/api/users/{user_id}")
def get_user(
    request: Request,
    user_id: int,
    _=require_permission(PERMISSIONS["view_users"])
):
    """API endpoint to get user details for editing"""

    try:
        # Get auto-logout setting for online/offline calculation
        auto_logout_setting = query("SELECT value FROM settings WHERE key = 'auto_logout_minutes'").mappings().first()
        auto_logout_minutes = int(auto_logout_setting["value"]) if auto_logout_setting else 60

        user = query("""
            SELECT id, username, first_name, last_name, email,
                   COALESCE(email_notifications, TRUE) AS email_notifications,
                   enabled, last_login, last_login_ip, created_at, last_seen
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        if not user:
            return JSONResponse({"error": "User not found"}, status_code=404)

        # Current user's ID for timezone conversion; resolve display prefs once
        # so the three format calls below don't each hit the database.
        current_user_id = int(request.session.get("user_id"))
        tz, date_format, time_format = get_display_prefs(current_user_id)

        # Get assigned role IDs
        role_rows = query("""
            SELECT role_id
            FROM user_roles
            WHERE user_id = :id
        """, {"id": user_id}).mappings().all()

        role_ids = [r["role_id"] for r in role_rows]

        # Calculate online status
        online_status = "offline"
        if user["last_seen"]:
            # Use the same logic as in the users list
            now_check = query("SELECT CASE WHEN NOW() - :last_seen > (INTERVAL '1 minute' * :minutes) THEN 'offline' ELSE 'online' END AS status", 
                            {"last_seen": user["last_seen"], "minutes": auto_logout_minutes}).mappings().first()
            online_status = now_check["status"]

        return {
            "id": user["id"],
            "username": user["username"],
            "first_name": user["first_name"] or "",
            "last_name": user["last_name"] or "",
            "email": user["email"] or "",
            "role_ids": role_ids,
            "email_notifications": user["email_notifications"],
            "enabled": user["enabled"],
            "last_login": format_datetime(user["last_login"], current_user_id, tz=tz, date_format=date_format, time_format=time_format)
                if user["last_login"] else None,
            "last_login_ip": user["last_login_ip"] or None,
            "created_at": format_datetime(user["created_at"], current_user_id, tz=tz, date_format=date_format, time_format=time_format)
                if user["created_at"] else None,
            "last_seen": format_datetime(user["last_seen"], current_user_id, tz=tz, date_format=date_format, time_format=time_format)
                if user["last_seen"] else None,
            "online_status": online_status
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
    _ = request_gettext(request)
    admin_username = request.session.get("username", "unknown")
    current_user_id = int(request.session.get("user_id"))

    # Normalize input
    username = username.strip()
    first_name = first_name.strip() or None
    last_name = last_name.strip() or None
    email = email.strip() or None

    # Validate username
    if len(username) < 3:
        flash(request, _("Username must be at least 3 characters long."), "error")
        return RedirectResponse("/users", status_code=303)

    # Unique username check
    existing = query("""
        SELECT id FROM users
        WHERE username = :u AND id != :id
    """, {"u": username, "id": user_id}).mappings().first()

    if existing:
        flash(request, _("Username '{0}' already exists.").format(username), "error")
        return RedirectResponse("/users", status_code=303)

    # Email validation
    if email and not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        flash(request, _("Invalid email format."), "error")
        return RedirectResponse("/users", status_code=303)

    # Must have at least one role
    if not role_ids:
        flash(request, _("At least one role must be assigned."), "error")
        return RedirectResponse("/users", status_code=303)

    # Normalize + dedupe role ids before touching the DB
    try:
        new_role_ids = sorted({int(r) for r in role_ids})
    except (TypeError, ValueError):
        flash(request, _("Invalid role selection."), "error")
        return RedirectResponse("/users", status_code=303)

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

        current_role_ids = {int(r["role_id"]) for r in current_role_rows}

        # Prevent self-lockout: user cannot remove their own admin-level access
        if user_id == current_user_id:
            # Check if the new role set still includes a role with manage_users permission
            admin_roles = query("""
                SELECT rp.role_id
                FROM role_permissions rp
                JOIN permissions p ON p.id = rp.permission_id
                WHERE p.name = 'manage_users'
            """).mappings().all()

            admin_role_ids = {int(r["role_id"]) for r in admin_roles}

            if not (set(new_role_ids) & admin_role_ids):
                flash(request, _("You cannot remove your own administrative access."), "error")
                return RedirectResponse("/users", status_code=303)

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
            and current_role_ids == set(new_role_ids)
        ):
            flash(request, _("No changes detected."), "info")
            return RedirectResponse("/users", status_code=303)

        # Only guard the assignment when the role set actually changes, so a user
        # demoted by someone else can still update their own profile.
        if current_role_ids != set(new_role_ids):
            error = check_privileged_role_assignment(request, new_role_ids)
            if error:
                flash(request, error, "error")
                return RedirectResponse("/users", status_code=303)

        # Password policy is validated up front so the failure surfaces as a
        # specific message instead of a generic transaction rollback.
        if password and (
            len(password) < 8
            or not re.search(r"[a-z]", password)
            or not re.search(r"[A-Z]", password)
            or not re.search(r"[0-9]", password)
        ):
            flash(request, _("Password must include upper, lower, number and be 8+ chars."), "error")
            return RedirectResponse("/users", status_code=303)

        assigned_by = request.session.get("user_id")
        assigned_by = int(assigned_by) if assigned_by else None

        # Update the user record and replace their role assignments atomically so
        # a failure cannot leave the user updated but with no roles (lockout).
        with transaction() as conn:
            # Password update
            if password:
                hash_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

                conn.execute(text("""
                    UPDATE users
                    SET username = :u, first_name = :fn, last_name = :ln,
                        email = :e, email_notifications = :enf,
                        enabled = :en, password_hash = :h
                    WHERE id = :id
                """), {
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
                conn.execute(text("""
                    UPDATE users
                    SET username = :u, first_name = :fn, last_name = :ln,
                        email = :e, email_notifications = :enf,
                        enabled = :en
                    WHERE id = :id
                """), {
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

            # Replace role assignments
            conn.execute(
                text("DELETE FROM user_roles WHERE user_id = :id"),
                {"id": user_id},
            )
            for rid in new_role_ids:
                conn.execute(text("""
                    INSERT INTO user_roles (user_id, role_id, assigned_by)
                    VALUES (:user_id, :role_id, :assigned_by)
                """), {"user_id": user_id, "role_id": rid, "assigned_by": assigned_by})

        flash(request, _("User updated successfully."), "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to update user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, _("User update failed. Please try again."), "error")

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
    _ = request_gettext(request)
    current_user_id = int(request.session.get("user_id"))
    admin_username = request.session.get("username", "unknown")

    if user_id == current_user_id:
        flash(request, _("You cannot delete your own account."), "error")
        return RedirectResponse("/users", status_code=303)

    try:
        user = query("""
            SELECT username FROM users WHERE id = :id
        """, {"id": user_id}).mappings().first()

        username = user["username"] if user else f"ID {user_id}"

        execute("DELETE FROM users WHERE id = :id", {"id": user_id})

        log("info", "Users",
            f"Admin '{admin_username}' deleted user '{username}' (ID: {user_id})")

        flash(request, _("User deleted successfully."), "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to delete user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, _("User deletion failed. Please try again."), "error")

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
    _ = request_gettext(request)
    current_user_id = int(request.session.get("user_id"))
    admin_username = request.session.get("username", "unknown")

    if user_id == current_user_id:
        flash(request, _("You cannot disable your own account."), "error")
        return RedirectResponse("/users", status_code=303)

    try:
        user = query("""
            SELECT username, enabled
            FROM users
            WHERE id = :id
        """, {"id": user_id}).mappings().first()

        if not user:
            flash(request, _("User not found."), "error")
            return RedirectResponse("/users", status_code=303)

        execute("""
            UPDATE users
            SET enabled = NOT enabled
            WHERE id = :id
        """, {"id": user_id})

        new_status = "disabled" if user["enabled"] else "enabled"

        log("info", "Users",
            f"Admin '{admin_username}' {new_status} user '{user['username']}' (ID: {user_id})")

        flash(request, _("User status updated successfully."), "success")

    except Exception as e:
        log("error", "Users",
            f"Failed to toggle user {user_id} by admin '{admin_username}': {str(e)}")
        flash(request, _("User status update failed. Please try again."), "error")

    return RedirectResponse("/users", status_code=303)