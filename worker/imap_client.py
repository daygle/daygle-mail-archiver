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
    config.reload()

    log.info(
        f"[{config.name}] Connecting to IMAP {config.host}:{config.port} "
        f"(SSL={config.use_ssl}, STARTTLS={config.require_starttls})"
    )

    if config.use_ssl:
        ctx = create_ssl_context(config.ca_bundle)
        client = IMAPClient(
            config.host,
            port=config.port,
            ssl=True,
            ssl_context=ctx,
            timeout=30,
        )
        log.info(f"[{config.name}] SSL/TLS connection established")
        return client

    client = IMAPClient(
        config.host,
        port=config.port,
        ssl=False,
        timeout=30,
    )

    if config.port == 143 and config.require_starttls:
        caps = client.capabilities()
        if b"STARTTLS" not in caps:
            raise RuntimeError("IMAP server does not support STARTTLS")
        log.info(f"[{config.name}] Starting STARTTLS negotiation")
        client.starttls(create_ssl_context(config.ca_bundle))
        log.info(f"[{config.name}] STARTTLS negotiation successful")

    return client