import os

class Config:
    DB_DSN = os.getenv("DB_DSN")
    IMAP_HOST = os.getenv("IMAP_HOST")
    IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
    IMAP_USER = os.getenv("IMAP_USER")
    IMAP_PASSWORD = os.getenv("IMAP_PASSWORD")
    IMAP_USE_SSL = os.getenv("IMAP_USE_SSL", "true").lower() == "true"

    POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 300))
    DELETE_AFTER_PROCESSING = os.getenv("DELETE_AFTER_PROCESSING", "false").lower() == "true"

    STORAGE_DIR = os.getenv("STORAGE_DIR", "/data/mail")