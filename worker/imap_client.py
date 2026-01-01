from imapclient import IMAPClient
from config import config
import ssl
import logging

log = logging.getLogger("imap")


def create_ssl_context(ca_bundle: str | None):
    ctx = ssl.create_default_context()

    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)

    return ctx


def connect():
    # Reload config each time we connect (dynamic settings)
    config.reload()

    log.info(
        f"Connecting to IMAP {config.IMAP_HOST}:{config.IMAP_PORT} "
        f"(SSL={config.IMAP_USE_SSL}, STARTTLS={config.IMAP_REQUIRE_STARTTLS})"
    )

    # SSL (IMAPS)
    if config.IMAP_USE_SSL:
        ssl_context = create_ssl_context(config.IMAP_CA_BUNDLE)
        client = IMAPClient(
            config.IMAP_HOST,
            port=config.IMAP_PORT,
            ssl=True,
            ssl_context=ssl_context,
            timeout=30,
        )
        log.info("SSL/TLS connection established")
        return client

    # Non‑SSL (STARTTLS)
    client = IMAPClient(
        config.IMAP_HOST,
        port=config.IMAP_PORT,
        ssl=False,
        timeout=30,
    )

    if config.IMAP_PORT == 143 and config.IMAP_REQUIRE_STARTTLS:
        caps = client.capabilities()
        if b"STARTTLS" not in caps:
            raise RuntimeError("IMAP server does not support STARTTLS")

        log.info("Starting STARTTLS negotiation")
        client.starttls(create_ssl_context(config.IMAP_CA_BUNDLE))
        log.info("STARTTLS negotiation successful")

    return client