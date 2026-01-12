from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from typing import List
import re

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates
from utils.permissions import require_permission, PERMISSIONS

router = APIRouter()


def flash(request: Request, message, category: str = "info"):
    request.session["flash"] = (
        message if isinstance(message, dict) else {"message": message, "type": category}
    )


# ---------------------------------------------------------
# LIST ROLES
# ---------------------------------------------------------
@router.get("/roles")
def list_roles(
    request: Request,
    _=require_permission(PERMISSIONS["manage_roles"])
):
    roles = query("""
        SELECT r.id, r.name, r.display_name, r.description, r.is_system_role,
               COUNT(rp.permission_id) AS permission_count,
               COALESCE(STRING_AGG(p.name, ', '), '') AS permissions
        FROM roles r
        LEFT JOIN role_permissions rp ON r.id = rp.role_id
        LEFT JOIN permissions p ON rp.permission_id = p.id
        GROUP BY r.id
        ORDER BY r.name
    """).mappings().all()

    permissions = query("""
        SELECT id, name, description
        FROM permissions
        ORDER BY name
    """).mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "roles.html",
        {"request": request, "roles": roles, "permissions": permissions, "flash": msg},
    )


# ---------------------------------------------------------
# CREATE ROLE
# ---------------------------------------------------------
@router.post("/roles/create")
def create_role(
    request: Request,
    _=require_permission(PERMISSIONS["manage_roles"]),
    name: str = Form(...),
    description: str = Form(""),
    permission_ids: List[str] = Form([]),
):
    try:
        raw = name.strip()

        # Clean display name
        display_name = re.sub(r"[_-]+", " ", raw).strip().title()

        # Clean slug
        slug = re.sub(r"[\s-]+", "_", raw).lower()

        if len(display_name) < 2:
            flash(request, "Role name must be at least 2 characters long.", "error")
            return RedirectResponse("/roles", status_code=303)

        # Check uniqueness
        existing = query(
            "SELECT id FROM roles WHERE name = :name", {"name": slug}
        ).first()
        if existing:
            flash(request, f"Role '{display_name}' already exists.", "error")
            return RedirectResponse("/roles", status_code=303)

        # Create role
        role_id = execute("""
            INSERT INTO roles (name, display_name, description)
            VALUES (:name, :display_name, :description)
        """, {
            "name": slug,
            "display_name": display_name,
            "description": description.strip() or None,
        })

        # Assign permissions
        for perm_id in permission_ids:
            try:
                execute("""
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (:role_id, :permission_id)
                """, {"role_id": role_id, "permission_id": int(perm_id)})
            except Exception as e:
                log("error", "Roles", f"Failed to add permission {perm_id} to role {role_id}: {str(e)}")

        flash(request, f"Role '{display_name}' created successfully.", "success")
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to create role '{name}': {str(e)}")
        flash(request, "Failed to create role.", "error")
        return RedirectResponse("/roles", status_code=303)


# ---------------------------------------------------------
# EDIT ROLE FORM
# ---------------------------------------------------------
@router.get("/roles/{role_id}/edit")
def edit_role_form(
    request: Request,
    role_id: int,
    _=require_permission(PERMISSIONS["manage_roles"])
):
    role = query("""
        SELECT id, name, display_name, description, is_system_role
        FROM roles
        WHERE id = :role_id
    """, {"role_id": role_id}).mappings().first()

    if not role:
        flash(request, "Role not found.", "error")
        return RedirectResponse("/roles", status_code=303)

    # Prevent editing system roles
    if role["is_system_role"]:
        flash(request, "System roles cannot be edited.", "error")
        return RedirectResponse("/roles", status_code=303)

    # Current permissions
    role_permissions = query("""
        SELECT permission_id
        FROM role_permissions
        WHERE role_id = :role_id
    """, {"role_id": role_id}).mappings().all()

    current_perm_ids = [rp["permission_id"] for rp in role_permissions]

    permissions = query("""
        SELECT id, name, description
        FROM permissions
        ORDER BY name
    """).mappings().all()

    return templates.TemplateResponse(
        "role-edit.html",
        {
            "request": request,
            "role": role,
            "permissions": permissions,
            "current_perm_ids": current_perm_ids,
        },
    )

@router.post("/roles/{role_id}/update")
def update_role(
    request: Request,
    role_id: int,
    name: str = Form(...),
    description: str = Form(""),
    permission_ids: List[str] = Form([])
):
    """Update an existing role"""
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    try:
        # Normalize and validate role name
        display_name = re.sub(r'[_\-]+', ' ', name.strip()).title()
        slug = re.sub(r'[\s\-]+', '_', display_name).lower()
        if not display_name or len(display_name) < 2:
            flash(request, "Role name must be at least 2 characters long", 'error')
            return RedirectResponse(f"/roles/{role_id}/edit", status_code=303)

        # Check if role slug already exists (excluding current role)
        existing = query("""
            SELECT id FROM roles
            WHERE name = :name AND id != :role_id
        """, {"name": slug, "role_id": role_id}).first()

        if existing:
            flash(request, f"Role '{display_name}' already exists", 'error')
            return RedirectResponse(f"/roles/{role_id}/edit", status_code=303)

        # Fetch current role and permissions to detect no-op
        current_role = query("SELECT name, display_name, description FROM roles WHERE id = :role_id", {"role_id": role_id}).mappings().first()
        current_perms = query("SELECT permission_id FROM role_permissions WHERE role_id = :role_id", {"role_id": role_id}).mappings().all()
        current_perm_ids = [p["permission_id"] for p in current_perms]

        # Compare normalized values
        new_display = display_name
        new_desc = description.strip() or None
        new_perm_set = set(int(p) for p in permission_ids) if permission_ids else set()
        current_perm_set = set(current_perm_ids)

        if current_role and current_role.get('display_name') == new_display and (current_role.get('description') or None) == new_desc and new_perm_set == current_perm_set and current_role.get('name') == slug:
            flash(request, "No changes detected.", 'info')
            return RedirectResponse("/roles", status_code=303)

        # Update the role
        execute("""
            UPDATE roles
            SET name = :name, display_name = :display_name, description = :description
            WHERE id = :role_id
        """, {
            "name": slug,
            "display_name": display_name,
            "description": new_desc,
            "role_id": role_id
        })

        # Remove existing permissions
        execute("DELETE FROM role_permissions WHERE role_id = :role_id", {"role_id": role_id})

        # Add new permissions
        if permission_ids:
            for perm_id in permission_ids:
                try:
                    execute("""
                        INSERT INTO role_permissions (role_id, permission_id)
                        VALUES (:role_id, :permission_id)
                    """, {"role_id": role_id, "permission_id": int(perm_id)})
                except Exception as e:
                    log("error", "Roles", f"Failed to add permission {perm_id} to role {role_id}: {str(e)}")

        log("info", "Roles", f"Updated role '{name}' with {len(permission_ids)} permissions")
        flash(request, f"Role '{name}' updated successfully", 'success')
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to update role {role_id}: {str(e)}")
        flash(request, "Failed to update role", 'error')
        return RedirectResponse(f"/roles/{role_id}/edit", status_code=303)

@router.post("/roles/{role_id}/delete")
def delete_role(request: Request, role_id: int):
    """Delete a role"""
    if not require_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    try:
        # Check if role is in use
        users_with_role = query("""
            SELECT COUNT(*) as count
            FROM user_roles
            WHERE role_id = :role_id
        """, {"role_id": role_id}).first()

        if users_with_role and users_with_role["count"] > 0:
            flash(request, "Cannot delete role that is assigned to users", 'error')
            return RedirectResponse("/roles", status_code=303)

        # Get role name for logging
        role = query("SELECT name FROM roles WHERE id = :role_id", {"role_id": role_id}).first()
        role_name = role["name"] if role else "Unknown"

        # Delete role permissions first
        execute("DELETE FROM role_permissions WHERE role_id = :role_id", {"role_id": role_id})

        # Delete the role
        execute("DELETE FROM roles WHERE id = :role_id", {"role_id": role_id})

        log("info", "Roles", f"Deleted role '{role_name}'")
        flash(request, f"Role '{role_name}' deleted successfully", 'success')
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to delete role {role_id}: {str(e)}")
        flash(request, "Failed to delete role", 'error')
        return RedirectResponse("/roles", status_code=303)