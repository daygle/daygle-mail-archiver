import os
from cryptography.fernet import Fernet
from fastapi import Request
from utils.db import query
from fastapi.responses import RedirectResponse
from utils.config import require_config

IMAP_PASSWORD_KEY = require_config("IMAP_PASSWORD_KEY")

fernet = Fernet(IMAP_PASSWORD_KEY.encode())

def encrypt_password(p: str) -> str:
    return fernet.encrypt(p.encode()).decode()

def decrypt_password(t: str) -> str:
    return fernet.decrypt(t.encode()).decode()

# Role-based access control helpers
def is_admin(request: Request) -> bool:
    """Check if the current user is an administrator"""
    role = get_role(request)
    return role == 'administrator'

def is_read_only(request: Request) -> bool:
    """Check if the current user has read-only access"""
    role = get_role(request)
    return role == 'read_only'


def get_role(request: Request) -> str | None:
    """Return a normalized role string for the current session or DB fallback.

    Normalization rules:
    - Lowercase
    - Replace hyphens/spaces with underscore
    - Map common aliases (e.g., 'admin' -> 'administrator')

    Returns canonical role string (e.g., 'administrator') or None if unknown.
    """
    # Try session first
    r = request.session.get("role")
    if r:
        rs = normalize_role_str(r)
        if rs:
            return rs

    # Fallback: try DB lookup if we have a user_id
    _sess_uid = request.session.get("user_id")
    try:
        user_id = int(_sess_uid) if _sess_uid is not None else None
    except (TypeError, ValueError):
        user_id = None

    if user_id is None:
        return None

    try:
        row = query("SELECT role FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if row and row.get('role'):
            return normalize_role_str(row.get('role'))
    except Exception:
        pass

    return None


def normalize_role_str(val) -> str | None:
    """Normalize a raw role value to canonical string or None.

    Examples: 'Admin' -> 'administrator', 'read-only' -> 'read_only'
    """
    if val is None:
        return None
    try:
        rs = str(val).strip().lower()
    except Exception:
        return None
    rs = rs.replace('-', '_').replace(' ', '_')
    if rs == 'admin':
        rs = 'administrator'
    return rs

def require_admin(request: Request):
    """Require administrator role - redirects if not admin"""
    if not is_admin(request):
        return RedirectResponse("/dashboard", status_code=303)
    return None

def can_delete(request: Request) -> bool:
    """Check if user can delete emails - only admins can delete"""
    return is_admin(request)

def can_modify_settings(request: Request) -> bool:
    """Check if user can modify system settings - only admins"""
    return is_admin(request)