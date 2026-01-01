from imapclient import IMAPClient
from config import Config
import ssl
import socket

def create_ssl_context():
    ctx = ssl.create_default_context()

    # Enforce certificate validation
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    # Optional: allow user to specify custom CA bundle
    if hasattr(Config, "IMAP_CA_BUNDLE") and Config.IMAP_CA_BUNDLE:
        ctx.load_verify_locations(Config.IMAP_CA_BUNDLE)

    return ctx


def connect():
    ssl_context = create_ssl_context() if Config.IMAP_USE_SSL else None

    return IMAPClient(
        Config.IMAP_HOST,
        port=Config.IMAP_PORT,
        ssl=Config.IMAP_USE_SSL,
        ssl_context=ssl_context,
        timeout=30,
    )