from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from typing import List
import re

from ..utils.db import query, transaction
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.i18n import request_gettext
from ..utils.permissions import (
    require_permission,
    PERMISSIONS,
    check_privileged_grant,
)

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
    # Keep role metadata and permission names structured. The old comma-separated
    # STRING_AGG value made the template parse database output and made the UI
    # brittle if a permission label ever contained punctuation.
    role_rows = query("""
        SELECT r.id, r.name, r.display_name, r.description, r.is_system_role,
               COUNT(DISTINCT rp.permission_id) AS permission_count,
               COUNT(DISTINCT ur.user_id) AS user_count
        FROM roles r
        LEFT JOIN role_permissions rp ON r.id = rp.role_id
        LEFT JOIN user_roles ur ON r.id = ur.role_id
        GROUP BY r.id
        ORDER BY COALESCE(r.display_name, r.name)
    """).mappings().all()

    permissions = query("""
        SELECT id, name, description, category
        FROM permissions
        ORDER BY category, name
    """).mappings().all()

    role_permission_rows = query("""
        SELECT rp.role_id, p.name
        FROM role_permissions rp
        JOIN permissions p ON p.id = rp.permission_id
        ORDER BY rp.role_id, p.category, p.name
    """).mappings().all()
    permission_names = {}
    for row in role_permission_rows:
        permission_names.setdefault(row["role_id"], []).append(row["name"])

    roles = []
    for row in role_rows:
        role = dict(row)
        role["permissions"] = permission_names.get(row["id"], [])
        roles.append(role)

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
    _ = request_gettext(request)
    try:
        raw = name.strip()

        # Clean display name
        display_name = re.sub(r"[_-]+", " ", raw).strip().title()

        # Clean slug
        slug = re.sub(r"[\s-]+", "_", raw).lower()

        if len(display_name) < 2:
            flash(request, _("Role name must be at least 2 characters long."), "error")
            return RedirectResponse("/roles", status_code=303)

        # Check uniqueness
        existing = query(
            "SELECT id FROM roles WHERE name = :name", {"name": slug}
        ).first()
        if existing:
            flash(request, _("Role '{0}' already exists.").format(display_name), "error")
            return RedirectResponse("/roles", status_code=303)

        # Normalize + dedupe the submitted permission ids before touching the DB
        try:
            perm_ids = sorted({int(p) for p in permission_ids})
        except (TypeError, ValueError):
            flash(request, _("Invalid permission selection."), "error")
            return RedirectResponse("/roles", status_code=303)

        # A role manager cannot grant privileged permissions they do not hold
        error = check_privileged_grant(request, perm_ids)
        if error:
            flash(request, error, "error")
            return RedirectResponse("/roles", status_code=303)

        # Create the role and assign permissions atomically so a mid-flight
        # failure cannot leave a role with a partial permission set.
        with transaction() as conn:
            created = conn.execute(text("""
                INSERT INTO roles (name, display_name, description)
                VALUES (:name, :display_name, :description)
                RETURNING id
            """), {
                "name": slug,
                "display_name": display_name,
                "description": description.strip() or None,
            }).mappings().first()

            if not created or "id" not in created:
                raise RuntimeError("INSERT INTO roles did not return an id")

            role_id = int(created["id"])

            for perm_id in perm_ids:
                conn.execute(text("""
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (:role_id, :permission_id)
                """), {"role_id": role_id, "permission_id": perm_id})

        flash(request, _("Role '{0}' created successfully.").format(display_name), "success")
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to create role '{name}': {str(e)}")
        flash(request, _("Failed to create role."), "error")
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
    _ = request_gettext(request)

    role = query("""
        SELECT id, name, display_name, description, is_system_role
        FROM roles
        WHERE id = :role_id
    """, {"role_id": role_id}).mappings().first()

    if not role:
        flash(request, _("Role not found."), "error")
        return RedirectResponse("/roles", status_code=303)

    # Prevent editing system roles
    if role["is_system_role"]:
        flash(request, _("System roles cannot be edited."), "error")
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


# ---------------------------------------------------------
# UPDATE ROLE
# ---------------------------------------------------------
@router.post("/roles/{role_id}/update")
def update_role(
    request: Request,
    role_id: int,
    _=require_permission(PERMISSIONS["manage_roles"]),
    name: str = Form(...),
    description: str = Form(""),
    permission_ids: List[str] = Form([]),
):
    """Update an existing role"""

    _ = request_gettext(request)

    # Fetch role and check system-role protection
    role = query("""
        SELECT id, name, display_name, description, is_system_role
        FROM roles
        WHERE id = :role_id
    """, {"role_id": role_id}).mappings().first()

    if not role:
        flash(request, _("Role not found."), "error")
        return RedirectResponse("/roles", status_code=303)

    if role["is_system_role"]:
        flash(request, _("System roles cannot be modified."), "error")
        return RedirectResponse("/roles", status_code=303)

    try:
        raw = name.strip()

        # Clean display name
        display_name = re.sub(r"[_-]+", " ", raw).strip().title()

        # Clean slug
        slug = re.sub(r"[\s-]+", "_", raw).lower()

        if len(display_name) < 2:
            flash(request, _("Role name must be at least 2 characters long."), "error")
            return RedirectResponse("/roles", status_code=303)

        # Check uniqueness (excluding current role)
        existing = query("""
            SELECT id FROM roles
            WHERE name = :name AND id != :role_id
        """, {"name": slug, "role_id": role_id}).first()

        if existing:
            flash(request, _("Role '{0}' already exists.").format(display_name), "error")
            return RedirectResponse("/roles", status_code=303)

        # Current permissions
        current_perm_ids = [
            p["permission_id"]
            for p in query("""
                SELECT permission_id
                FROM role_permissions
                WHERE role_id = :role_id
            """, {"role_id": role_id}).mappings().all()
        ]
        current_perm_set = {int(p) for p in current_perm_ids}

        # Normalize + dedupe the submitted permission ids before touching the DB
        try:
            new_perm_set = {int(p) for p in permission_ids}
        except (TypeError, ValueError):
            flash(request, _("Invalid permission selection."), "error")
            return RedirectResponse("/roles", status_code=303)

        # Detect no-op
        if (
            role["display_name"] == display_name
            and (role["description"] or None) == (description.strip() or None)
            and role["name"] == slug
            and new_perm_set == current_perm_set
        ):
            flash(request, _("No changes detected."), "info")
            return RedirectResponse("/roles", status_code=303)

        # Only *additions* of privileged permissions the actor lacks are blocked;
        # removals are always allowed (least privilege).
        added = new_perm_set - current_perm_set
        error = check_privileged_grant(request, added)
        if error:
            flash(request, error, "error")
            return RedirectResponse("/roles", status_code=303)

        # Update role and replace its permissions atomically so a failure cannot
        # leave the role renamed with an empty or partial permission set.
        with transaction() as conn:
            conn.execute(text("""
                UPDATE roles
                SET name = :name, display_name = :display_name, description = :description
                WHERE id = :role_id
            """), {
                "name": slug,
                "display_name": display_name,
                "description": description.strip() or None,
                "role_id": role_id,
            })

            conn.execute(
                text("DELETE FROM role_permissions WHERE role_id = :role_id"),
                {"role_id": role_id},
            )

            for perm_id in sorted(new_perm_set):
                conn.execute(text("""
                    INSERT INTO role_permissions (role_id, permission_id)
                    VALUES (:role_id, :permission_id)
                """), {"role_id": role_id, "permission_id": perm_id})

        flash(request, _("Role '{0}' updated successfully.").format(display_name), "success")
        log("info", "Roles", f"Updated role '{display_name}' with {len(new_perm_set)} permissions")
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to update role {role_id}: {str(e)}")
        flash(request, _("Failed to update role."), "error")
        return RedirectResponse("/roles", status_code=303)


# ---------------------------------------------------------
# DELETE ROLE
# ---------------------------------------------------------
@router.post("/roles/{role_id}/delete")
def delete_role(
    request: Request,
    role_id: int,
    _=require_permission(PERMISSIONS["manage_roles"])
):
    """Delete a role"""

    _ = request_gettext(request)

    # Fetch role and check system-role protection
    role = query("""
        SELECT id, name, is_system_role
        FROM roles
        WHERE id = :role_id
    """, {"role_id": role_id}).mappings().first()

    if not role:
        flash(request, _("Role not found."), "error")
        return RedirectResponse("/roles", status_code=303)

    if role["is_system_role"]:
        flash(request, _("System roles cannot be deleted."), "error")
        return RedirectResponse("/roles", status_code=303)

    try:
        # Check if role is assigned to users
        count = query("""
            SELECT COUNT(*) AS count
            FROM user_roles
            WHERE role_id = :role_id
        """, {"role_id": role_id}).mappings().first()

        if count and count["count"] > 0:
            flash(request, _("Cannot delete a role that is assigned to users."), "error")
            return RedirectResponse("/roles", status_code=303)

        # Delete the role and its permission links atomically (the foreign keys
        # cascade anyway, but keeping both statements in one transaction makes
        # the intent explicit and safe).
        with transaction() as conn:
            conn.execute(
                text("DELETE FROM role_permissions WHERE role_id = :role_id"),
                {"role_id": role_id},
            )
            conn.execute(
                text("DELETE FROM roles WHERE id = :role_id"),
                {"role_id": role_id},
            )

        flash(request, _("Role '{0}' deleted successfully.").format(role['name']), "success")
        log("info", "Roles", f"Deleted role '{role['name']}'")
        return RedirectResponse("/roles", status_code=303)

    except Exception as e:
        log("error", "Roles", f"Failed to delete role {role_id}: {str(e)}")
        flash(request, _("Failed to delete role."), "error")
        return RedirectResponse("/roles", status_code=303)