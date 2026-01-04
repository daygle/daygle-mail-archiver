import os
from cryptography.fernet import Fernet

IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")
if not IMAP_PASSWORD_KEY:
    raise RuntimeError("IMAP_PASSWORD_KEY is not set")

fernet = Fernet(IMAP_PASSWORD_KEY.encode())

def decrypt_password(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()

def encrypt_password(password: str) -> str:
    """Encrypt a password for storage"""
    return fernet.encrypt(password.encode()).decode()