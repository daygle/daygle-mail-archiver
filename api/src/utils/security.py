import os
from cryptography.fernet import Fernet
from fastapi import Request
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
    r = request.session.get("role")
    if not r:
        return False
    try:
        rs = str(r).lower()
    except Exception:
        return False
    # Normalize common variants
    rs = rs.replace('-', '_')
    if rs == 'admin':
        rs = 'administrator'
    return rs == 'administrator'

def is_read_only(request: Request) -> bool:
    """Check if the current user has read-only access"""
    r = request.session.get("role")
    if not r:
        return False
    try:
        rs = str(r).lower()
    except Exception:
        return False
    rs = rs.replace('-', '_')
    return rs == 'read_only'

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