from typing import List, Dict, Any, Optional

from fastapi import Request, HTTPException, Depends

from .db import query
from .logger import log


class PermissionChecker:
    """
    Handles permission checking for users.

    Permissions are derived from:
      - user_roles (user_id -> role_id)
      - role_permissions (role_id -> permission_id)
      - permissions (permission_id -> name)
    """

    def __init__(self, request: Request):
        self.request = request
        self._permissions_cache: Optional[List[str]] = None

    # -----------------------------
    # Internal loading
    # -----------------------------
    def _load_user_permissions(self) -> List[str]:
        """
        Load all permissions for the current user.

        Returns an empty list if:
          - no user_id in session
          - query fails
        """
        if self._permissions_cache is not None:
            return self._permissions_cache

        user_id = self.request.session.get("user_id")
        if not user_id:
            return []

        try:
            rows = query(
                """
                SELECT DISTINCT p.name
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                JOIN user_roles ur ON rp.role_id = ur.role_id
                WHERE ur.user_id = :user_id
                """,
                {"user_id": user_id},
            ).mappings().all()

            self._permissions_cache = [row["name"] for row in rows]

            return self._permissions_cache

        except Exception as e:
            log("error", "Permissions", f"Failed to load permissions for user {user_id}: {str(e)}")
            return []

    # -----------------------------
    # Public API
    # -----------------------------
    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return permission in self._load_user_permissions()

    def get_user_permissions(self) -> List[str]:
        """Get all permissions for the current user."""
        return self._load_user_permissions()


# -----------------------------
# FastAPI dependencies
# -----------------------------
def require_permission(permission: str):
    async def dependency(request: Request):
        checker = PermissionChecker(request)
        if not checker.has_permission(permission):
            username = request.session.get("username", "unknown")
            log(
                "warning",
                "Security",
                f"Access denied for permission '{permission}' by user {username}",
            )
            raise HTTPException(
                status_code=403,
                detail={"error": "Insufficient permissions", "required": permission},
            )

    return Depends(dependency)


# -------------------------------------------------------------------------
# Privileged-permission grant guard
# -------------------------------------------------------------------------
# These permissions grant administrative control over the system. Granting them
# to a role (or assigning a role that carries them) must only be possible for
# users who already hold them; otherwise a user manager could craft a role with
# every permission and self-escalate to a full administrator.
PRIVILEGED_PERMISSIONS = frozenset(
    {
        "manage_users",
        "manage_roles",
        "manage_global_settings",
    }
)


def _validate_ids(raw_ids) -> Optional[set]:
    """Coerce an iterable of int-like ids to a set of ints.

    Returns None when any value is not a valid integer.
    """
    try:
        return {int(v) for v in raw_ids}
    except (TypeError, ValueError):
        return None


def _load_permission_name_by_id() -> Dict[int, str]:
    """Map permission id -> permission name (small, fully materialised)."""
    rows = query("SELECT id, name FROM permissions").mappings().all()
    return {int(row["id"]): row["name"] for row in rows}


def _load_privileged_permissions_for_roles(role_ids: set) -> set:
    """Return the set of privileged permission names carried by the given roles."""
    if not role_ids:
        return set()
    rows = query(
        """
        SELECT rp.role_id, p.name
        FROM role_permissions rp
        JOIN permissions p ON p.id = rp.permission_id
        """
    ).mappings().all()
    return {
        row["name"]
        for row in rows
        if int(row["role_id"]) in role_ids and row["name"] in PRIVILEGED_PERMISSIONS
    }


def check_privileged_grant(request: Request, permission_ids) -> Optional[str]:
    """Validate that the acting user may grant `permission_ids` to a role.

    Returns an error message when the actor attempts to grant a privileged
    permission they do not already hold, or None when the grant is allowed.
    Fails closed on invalid input or database errors.
    """
    wanted = _validate_ids(permission_ids)
    if wanted is None:
        return "Invalid permission selection."
    if not wanted:
        return None

    held = set(PermissionChecker(request).get_user_permissions())
    try:
        name_by_id = _load_permission_name_by_id()
    except Exception:
        return "Failed to validate permissions."

    # Reject unknown permission ids up front so a stale/malicious form value
    # fails with a clear message instead of a foreign-key violation mid-flight.
    unknown = sorted(wanted - set(name_by_id.keys()))
    if unknown:
        return "Unknown permission ids: " + ", ".join(str(v) for v in unknown)

    missing = sorted(
        name
        for pid, name in name_by_id.items()
        if pid in wanted and name in PRIVILEGED_PERMISSIONS and name not in held
    )
    if missing:
        return "You cannot grant permissions you do not hold: " + ", ".join(missing)
    return None


def check_privileged_role_assignment(request: Request, role_ids) -> Optional[str]:
    """Validate that the acting user may assign `role_ids` to a user.

    Returns an error message when the requested roles carry privileged
    permissions the actor does not hold, or None when the assignment is allowed.
    Fails closed on invalid input or database errors.

    Note: this is intentionally all-or-nothing on the requested set. A role set
    containing a privileged role the actor cannot fully administer is rejected
    wholesale, even when the change also adds benign roles.
    """
    wanted = _validate_ids(role_ids)
    if wanted is None:
        return "Invalid role selection."
    if not wanted:
        return "At least one role must be assigned."

    held = set(PermissionChecker(request).get_user_permissions())
    try:
        rows = query("SELECT id FROM roles").mappings().all()
        known_roles = {int(row["id"]) for row in rows}
    except Exception:
        return "Failed to validate roles."

    # Reject unknown role ids up front for a clear error message.
    unknown = sorted(wanted - known_roles)
    if unknown:
        return "Unknown role ids: " + ", ".join(str(v) for v in unknown)

    try:
        granted_privileged = _load_privileged_permissions_for_roles(wanted)
    except Exception:
        return "Failed to validate roles."

    missing = sorted(granted_privileged - held)
    if missing:
        return (
            "You cannot assign roles that grant permissions you do not hold: "
            + ", ".join(missing)
        )
    return None


# -----------------------------
# Permission constants
# -----------------------------
PERMISSIONS: Dict[str, str] = {
    "view_dashboard": "view_dashboard",
    "view_emails": "view_emails",
    "delete_emails": "delete_emails",
    "export_emails": "export_emails",
    "view_quarantine": "view_quarantine",
    "manage_quarantine": "manage_quarantine",
    "restore_quarantine": "restore_quarantine",
    "delete_quarantine": "delete_quarantine",
    "view_reports": "view_reports",
    "view_security_reports": "view_security_reports",
    "export_reports": "export_reports",
    "view_fetch_accounts": "view_fetch_accounts",
    "manage_fetch_accounts": "manage_fetch_accounts",
    "view_worker_status": "view_worker_status",
    "view_logs": "view_logs",
    "view_alerts": "view_alerts",
    "manage_alerts": "manage_alerts",
    "view_users": "view_users",
    "manage_users": "manage_users",
    "manage_roles": "manage_roles",
    "view_global_settings": "view_global_settings",
    "manage_global_settings": "manage_global_settings",
    "manage_own_profile": "manage_own_profile",
    "import_emails": "import_emails",
}