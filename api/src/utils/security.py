import os
from cryptography.fernet import Fernet

IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")
if not IMAP_PASSWORD_KEY:
    raise RuntimeError("IMAP_PASSWORD_KEY is not set")

fernet = Fernet(IMAP_PASSWORD_KEY.encode())

def encrypt_password(p: str) -> str:
    return fernet.encrypt(p.encode()).decode()

def decrypt_password(t: str) -> str:
    return fernet.decrypt(t.encode()).decode()