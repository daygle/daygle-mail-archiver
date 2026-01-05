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