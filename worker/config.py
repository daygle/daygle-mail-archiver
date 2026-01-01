import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet, InvalidToken

load_dotenv()

DB_DSN = os.getenv("DB_DSN")
IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")
IMAP_ACCOUNT_NAME = os.getenv("IMAP_ACCOUNT_NAME")

if not DB_DSN:
    raise RuntimeError("DB_DSN is not set")
if not IMAP_ACCOUNT_NAME:
    raise RuntimeError("IMAP_ACCOUNT_NAME is not set for this worker")

engine = create_engine(DB_DSN, future=True)


def get_fernet():
    if not IMAP_PASSWORD_KEY:
        return None
    return Fernet(IMAP_PASSWORD_KEY.encode("utf-8"))


def decrypt_password(token: str) -> str:
    if not token:
        return ""
    f = get_fernet()
    if not f:
        return ""
    try:
        return f.decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return ""


def load_global_settings() -> dict:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT key, value FROM settings")
        ).mappings().all()
    return {r["key"]: r["value"] for r in rows}


class AccountConfig:
    """
    Per-account configuration loaded from imap_accounts + global settings.
    """

    def __init__(self):
        self.reload()

    def reload(self):
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT *
                    FROM imap_accounts
                    WHERE name = :name AND enabled = TRUE
                    """
                ),
                {"name": IMAP_ACCOUNT_NAME},
            ).mappings().first()

        if not row:
            raise RuntimeError(
                f"IMAP account '{IMAP_ACCOUNT_NAME}' not found or disabled"
            )

        self.id = row["id"]
        self.name = row["name"]
        self.host = row["host"]
        self.port = row["port"]
        self.username = row["username"]
        self.password = decrypt_password(row["password_encrypted"])
        self.use_ssl = row["use_ssl"]
        self.require_starttls = row["require_starttls"]
        self.ca_bundle = row["ca_bundle"]
        self.poll_interval_seconds = row["poll_interval_seconds"]
        self.delete_after_processing = row["delete_after_processing"]

        # Global settings (storage dir, etc.)
        g = load_global_settings()
        self.storage_dir = g.get("storage_dir", "/data/mail")


config = AccountConfig()