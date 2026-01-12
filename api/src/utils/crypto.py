from cryptography.fernet import Fernet
from utils.config import require_config

IMAP_PASSWORD_KEY = require_config("IMAP_PASSWORD_KEY")
fernet = Fernet(IMAP_PASSWORD_KEY.encode())

def encrypt_password(p: str) -> str:
    return fernet.encrypt(p.encode()).decode()

def decrypt_password(t: str) -> str:
    return fernet.decrypt(t.encode()).decode()