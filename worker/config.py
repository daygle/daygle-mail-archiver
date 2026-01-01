import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # IMAP connection
    IMAP_HOST = os.getenv("IMAP_HOST")
    IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
    IMAP_USER = os.getenv("IMAP_USER")
    IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
    IMAP_USE_SSL = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

    # IMAP security enhancements
    IMAP_REQUIRE_STARTTLS = os.getenv("IMAP_REQUIRE_STARTTLS", "false").lower() == "true"
    IMAP_CA_BUNDLE = os.getenv("IMAP_CA_BUNDLE")  # optional custom CA path

    # Worker settings
    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
    DELETE_AFTER_PROCESSING = os.getenv("DELETE_AFTER_PROCESSING", "false").lower() == "true"

    # Storage
    STORAGE_DIR = os.getenv("STORAGE_DIR", "/data/mail")

    # Database
    DB_DSN = os.getenv("DB_DSN")