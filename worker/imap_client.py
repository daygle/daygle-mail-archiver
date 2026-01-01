from imapclient import IMAPClient
from config import Config
import ssl
import logging
import os

log = logging.getLogger("imap")


def create_ssl_context():
    ctx = ssl.create_default_context()

    # Enforce strict certificate validation
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    # Optional custom CA bundle
    if Config.IMAP_CA_BUNDLE:
        ctx.load_verify_locations(Config.IMAP_CA_BUNDLE)

    return ctx


def connect():
    log.info(
        f"Connecting to IMAP server {Config.IMAP_HOST}:{Config.IMAP_PORT} "
        f"(SSL={Config.IMAP_USE_SSL}, REQUIRE_STARTTLS={Config.IMAP_REQUIRE_STARTTLS})"
    )

    # SSL connection (IMAPS, port 993)
    if Config.IMAP_USE_SSL:
        ssl_context = create_ssl_context()
        client = IMAPClient(
            Config.IMAP_HOST,
            port=Config.IMAP_PORT,
            ssl=True,
            ssl_context=ssl_context,
            timeout=30,
        )
        log.info("SSL/TLS connection established")
        return client

    # Non-SSL connection (port 143)
    client = IMAPClient(
        Config.IMAP_HOST,
        port=Config.IMAP_PORT,
        ssl=False,
        timeout=30,
    )

    # STARTTLS enforcement
    if Config.IMAP_PORT == 143 and Config.IMAP_REQUIRE_STARTTLS:
        capabilities = client.capabilities()

        if b"STARTTLS" not in capabilities:
            raise RuntimeError(
                "IMAP server does not support STARTTLS but IMAP_REQUIRE_STARTTLS=true"
            )

        log.info("Starting STARTTLS negotiation")
        client.starttls(create_ssl_context())
        log.info("STARTTLS negotiation successful")

    return client