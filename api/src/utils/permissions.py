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

            # Optional: cache in session for the duration of the session
            cache_key = f"user_permissions_{user_id}"
            self.request.session[cache_key] = self._permissions_cache

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

    def has_any_permission(self, permissions: List[str]) -> bool:
        """Check if user has any of the specified permissions."""
        user_permissions = self._load_user_permissions()
        return any(perm in user_permissions for perm in permissions)

    def has_all_permissions(self, permissions: List[str]) -> bool:
        """Check if user has all of the specified permissions."""
        user_permissions = self._load_user_permissions()
        return all(perm in user_permissions for perm in permissions)

    def get_user_permissions(self) -> List[str]:
        """Get all permissions for the current user."""
        return self._load_user_permissions()

    def get_user_roles(self) -> List[Dict[str, Any]]:
        """Get all roles for the current user."""
        user_id = self.request.session.get("user_id")
        if not user_id:
            return []

        try:
            rows = query(
                """
                SELECT r.id, r.name, r.description
                FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = :user_id
                ORDER BY r.name
                """,
                {"user_id": user_id},
            ).mappings().all()
            return [dict(row) for row in rows]
        except Exception as e:
            log("error", "Permissions", f"Failed to load roles for user {user_id}: {str(e)}")
            return []


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


def require_any_permission(permissions: List[str]):
    async def dependency(request: Request):
        checker = PermissionChecker(request)
        if not checker.has_any_permission(permissions):
            username = request.session.get("username", "unknown")
            log(
                "warning",
                "Security",
                f"Access denied for any of permissions {permissions} by user {username}",
            )
            raise HTTPException(
                status_code=403,
                detail={"error": "Insufficient permissions", "required_any": permissions},
            )

    return Depends(dependency)


def get_permission_checker(request: Request) -> PermissionChecker:
    return PermissionChecker(request)


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