"""
Permission system utilities for Daygle Mail Archiver
Provides granular permission checking and role management
"""

from typing import List, Dict, Any, Optional
from fastapi import Request


class PermissionChecker:
    """Handles permission checking for users"""

    def __init__(self, request: Request):
        self.request = request
        self._permissions_cache: Optional[List[str]] = None

    def _load_user_permissions(self) -> List[str]:
        """Load all permissions for the current user"""
        if self._permissions_cache is not None:
            return self._permissions_cache

        user_id = self.request.session.get("user_id")
        if not user_id:
            return []

        try:
            # Lazy import to avoid circular dependencies
            from utils.db import query
            # Get all permissions for user's roles
            permissions = query("""
                SELECT DISTINCT p.name
                FROM permissions p
                JOIN role_permissions rp ON p.id = rp.permission_id
                JOIN user_roles ur ON rp.role_id = ur.role_id
                WHERE ur.user_id = :user_id
            """, {"user_id": user_id}).mappings().all()

            self._permissions_cache = [p["name"] for p in permissions]
            return self._permissions_cache
        except Exception as e:
            # Lazy import logger
            from utils.logger import log
            log("error", "Permissions", f"Failed to load permissions for user {user_id}: {str(e)}")
            return []

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission"""
        permissions = self._load_user_permissions()
        return permission in permissions

    def has_any_permission(self, permissions: List[str]) -> bool:
        """Check if user has any of the specified permissions"""
        user_permissions = self._load_user_permissions()
        return any(perm in user_permissions for perm in permissions)

    def has_all_permissions(self, permissions: List[str]) -> bool:
        """Check if user has all of the specified permissions"""
        user_permissions = self._load_user_permissions()
        return all(perm in user_permissions for perm in permissions)

    def get_user_permissions(self) -> List[str]:
        """Get all permissions for the current user"""
        return self._load_user_permissions()

    def get_user_roles(self) -> List[Dict[str, Any]]:
        """Get all roles for the current user"""
        user_id = self.request.session.get("user_id")
        if not user_id:
            return []

        try:
            roles = query("""
                SELECT r.id, r.name, r.description
                FROM roles r
                JOIN user_roles ur ON r.id = ur.role_id
                WHERE ur.user_id = :user_id
                ORDER BY r.name
            """, {"user_id": user_id}).mappings().all()
            return [dict(role) for role in roles]
        except Exception as e:
            log("error", "Permissions", f"Failed to load roles for user {user_id}: {str(e)}")
            return []


def require_permission(permission: str):
    """Decorator to require a specific permission for a route"""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            checker = PermissionChecker(request)
            if not checker.has_permission(permission):
                log("warning", "Security", f"Access denied for permission '{permission}' by user {request.session.get('username', 'unknown')}")
                # Return 403 Forbidden
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    {"error": "Insufficient permissions", "required": permission},
                    status_code=403
                )
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def require_any_permission(permissions: List[str]):
    """Decorator to require any of the specified permissions for a route"""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            checker = PermissionChecker(request)
            if not checker.has_any_permission(permissions):
                log("warning", "Security", f"Access denied for any of permissions {permissions} by user {request.session.get('username', 'unknown')}")
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    {"error": "Insufficient permissions", "required_any": permissions},
                    status_code=403
                )
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator


def get_permission_checker(request: Request) -> PermissionChecker:
    """Get a permission checker instance for the current request"""
    return PermissionChecker(request)


# Permission constants for easy reference
PERMISSIONS = {
    # Dashboard & Overview
    'view_dashboard': 'view_dashboard',

    # Email Management
    'view_emails': 'view_emails',
    'delete_emails': 'delete_emails',
    'export_emails': 'export_emails',

    # Quarantine Management
    'view_quarantine': 'view_quarantine',
    'manage_quarantine': 'manage_quarantine',

    # Reports & Analytics
    'view_reports': 'view_reports',
    'export_reports': 'export_reports',

    # Account Management
    'view_fetch_accounts': 'view_fetch_accounts',
    'manage_fetch_accounts': 'manage_fetch_accounts',

    # System Monitoring
    'view_worker_status': 'view_worker_status',
    'view_logs': 'view_logs',

    # Alert Management
    'view_alerts': 'view_alerts',
    'manage_alerts': 'manage_alerts',

    # User Management
    'view_users': 'view_users',
    'manage_users': 'manage_users',
    'manage_roles': 'manage_roles',

    # System Settings
    'view_global_settings': 'view_global_settings',
    'manage_global_settings': 'manage_global_settings',

    # Personal Settings
    'manage_own_profile': 'manage_own_profile'
}