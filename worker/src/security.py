import os
from cryptography.fernet import Fernet
from config import require_config

IMAP_PASSWORD_KEY = require_config("IMAP_PASSWORD_KEY")

fernet = Fernet(IMAP_PASSWORD_KEY.encode())

def decrypt_password(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()

def encrypt_password(password: str) -> str:
    """Encrypt a password for storage"""
    return fernet.encrypt(password.encode()).decode()

# Optional quarantine encryption helpers (explicitly do NOT reuse IMAP key)
from config import get_config
import logging

logger = logging.getLogger(__name__)

_quarantine_fernet = None
if get_config('CLAMAV_QUARANTINE_KEY'):
    try:
        _quarantine_fernet = Fernet(get_config('CLAMAV_QUARANTINE_KEY').encode())
    except Exception as e:
        logger.warning("Failed to initialise CLAMAV quarantine Fernet: %s", e)
        _quarantine_fernet = None
else:
    # IMPORTANT: We intentionally do NOT fall back to IMAP_PASSWORD_KEY for quarantine encryption.
    # If a separate CLAMAV_QUARANTINE_KEY is not provided, quarantine encryption will remain disabled.
    logger.info("No CLAMAV_QUARANTINE_KEY configured; quarantine encryption disabled (no IMAP key fallback)")


def encrypt_quarantine(data: bytes) -> bytes:
    if not _quarantine_fernet:
        return data
    return _quarantine_fernet.encrypt(data)


def decrypt_quarantine(token: bytes) -> bytes:
    if not _quarantine_fernet:
        return token
    return _quarantine_fernet.decrypt(token)