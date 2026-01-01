import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

# -------------------------------------------------------------------
# Load bootstrap defaults from .env
# -------------------------------------------------------------------

DB_DSN = os.getenv("DB_DSN")
IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")

# Storage default (can be overridden by DB)
STORAGE_DIR_DEFAULT = os.getenv("STORAGE_DIR", "/data/mail")

# -------------------------------------------------------------------
# DB connection
# -------------------------------------------------------------------

engine = create_engine(DB_DSN, future=True)


# -------------------------------------------------------------------
# Encryption helpers
# -------------------------------------------------------------------

def get_fernet():
    if not IMAP_PASSWORD_KEY:
        return None
    return Fernet(IMAP_PASSWORD_KEY.encode("utf-8"))


def decrypt_imap_password(token: str) -> str:
    if not token:
        return ""
    f = get_fernet()
    if not f:
        return ""
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


# -------------------------------------------------------------------
# Load settings from DB
# -------------------------------------------------------------------

def load_db_settings() -> dict:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT key, value FROM settings")).mappings().all()
    return {r["key"]: r["value"] for r in rows}


# -------------------------------------------------------------------
# Final merged config object
# -------------------------------------------------------------------

class Config:
    """
    Config is dynamically loaded from DB settings, with .env as fallback.
    """

    def __init__(self):
        self.reload()

    def reload(self):
        db = load_db_settings()

        # IMAP settings
        self.IMAP_HOST = db.get("imap_host", os.getenv("IMAP_HOST"))
        self.IMAP_PORT = int(db.get("imap_port", os.getenv("IMAP_PORT", "993")))
        self.IMAP_USER = db.get("imap_user", os.getenv("IMAP_USER"))

        encrypted_pw = db.get("imap_password_encrypted", "")
        if encrypted_pw:
            self.IMAP_PASSWORD = decrypt_imap_password(encrypted_pw)
        else:
            self.IMAP_PASSWORD = os.getenv("IMAP_PASSWORD", "")

        self.IMAP_USE_SSL = db.get("imap_use_ssl", "true").lower() == "true"
        self.IMAP_REQUIRE_STARTTLS = db.get("imap_require_starttls", "false").lower() == "true"
        self.IMAP_CA_BUNDLE = db.get("imap_ca_bundle", "")

        # Worker settings
        self.POLL_INTERVAL_SECONDS = int(
            db.get("poll_interval_seconds", os.getenv("POLL_INTERVAL_SECONDS", "300"))
        )
        self.DELETE_AFTER_PROCESSING = (
            db.get("delete_after_processing", "false").lower() == "true"
        )

        # Storage
        self.STORAGE_DIR = db.get("storage_dir", STORAGE_DIR_DEFAULT)

        # UI settings (not used by worker, but loaded for completeness)
        self.PAGE_SIZE = int(db.get("page_size", "50"))


# Singleton config instance
config = Config()