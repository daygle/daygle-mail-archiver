import os
import asyncio
import ssl
import logging
import gzip
from datetime import datetime, timezone
from typing import Any, Dict, List

import aioimaplib
from sqlalchemy import create_engine, text
from mailparser import parse_from_bytes
from cryptography.fernet import Fernet


# ------------------
# Configuration
# ------------------

DB_DSN = os.getenv("DB_DSN")
if not DB_DSN:
    raise RuntimeError("DB_DSN environment variable is not set")

FERNET_KEY = os.getenv("FERNET_KEY")
if not FERNET_KEY:
    raise RuntimeError("FERNET_KEY environment variable is not set")

# You can override this if you ever want per-worker settings
DEFAULT_FOLDER = "INBOX"

engine = create_engine(DB_DSN, future=True)
fernet = Fernet(FERNET_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [worker] %(message)s",
)
logger = logging.getLogger("daygle-worker")


# ------------------
# DB helpers
# ------------------

def decrypt_password(token: str) -> str:
    """
    Decrypts password stored in imap_accounts.password_encrypted.
    Must match the encryption logic in the API.
    """
    return fernet.decrypt(token.encode()).decode()


def load_imap_sources() -> List[Dict[str, Any]]:
    """
    Load enabled IMAP accounts from the database.
    Expects an imap_accounts table similar to the API.
    """
    sql = """
        SELECT
            id,
            name,
            host,
            port,
            username,
            password_encrypted,
            use_ssl,
            require_starttls,
            ca_bundle,
            poll_interval_seconds,
            delete_after_processing,
            enabled
        FROM imap_accounts
        WHERE enabled = TRUE
        ORDER BY id
    """
    with engine.begin() as conn:
        rows = conn.execute(text(sql)).mappings().all()
        return [dict(r) for r in rows]


def update_heartbeat(account_id: int, success: bool, error: str | None = None) -> None:
    """
    Update last_heartbeat, last_success, last_error on imap_accounts.
    """
    with engine.begin() as conn:
        if success:
            conn.execute(
                text(
                    """
                    UPDATE imap_accounts
                    SET last_heartbeat = NOW(),
                        last_success = NOW(),
                        last_error = NULL
                    WHERE id = :id
                    """
                ),
                {"id": account_id},
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE imap_accounts
                    SET last_heartbeat = NOW(),
                        last_error = :err
                    WHERE id = :id
                    """
                ),
                {"id": account_id, "err": error or "Unknown error"},
            )


def log_error(source_name: str, message: str, details: str | None = None) -> None:
    """
    Insert an entry into error_log (optional but useful).
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO error_log (timestamp, source, message, details)
                VALUES (NOW(), :source, :message, :details)
                """
            ),
            {
                "source": source_name,
                "message": message,
                "details": details or "",
            },
        )


def insert_message(
    source: Dict[str, Any],
    folder: str,
    uid: str,
    raw_email: bytes,
) -> None:
    """
    Compress raw email, parse headers, and insert into messages table.

    Schema (fresh design):

        CREATE TABLE messages (
            id SERIAL PRIMARY KEY,
            source TEXT NOT NULL,
            folder TEXT NOT NULL,
            uid TEXT NOT NULL,
            subject TEXT,
            sender TEXT,
            recipients TEXT,
            date TIMESTAMPTZ,
            raw_email BYTEA NOT NULL,
            compressed BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """
    # Compress
    compressed_email = gzip.compress(raw_email)

    # Parse headers with mailparser
    parsed = parse_from_bytes(raw_email)

    subject = parsed.subject or ""

    if parsed.from_:
        # parsed.from_ is a list of (name, email)
        sender = parsed.from_[0][1] or parsed.from_[0][0] or ""
    else:
        sender = ""

    recipients_list = [addr[1] for addr in (parsed.to or [])]
    recipients = ", ".join(recipients_list)

    msg_date = parsed.date  # datetime or None
    if isinstance(msg_date, datetime):
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        date_value = msg_date
    else:
        date_value = None

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO messages
                    (source, folder, uid, subject, sender, recipients, date, raw_email, compressed, created_at)
                VALUES
                    (:source, :folder, :uid, :subject, :sender, :recipients, :date, :raw_email, TRUE, NOW())
                """
            ),
            {
                "source": source["name"],
                "folder": folder,
                "uid": uid,
                "subject": subject,
                "sender": sender,
                "recipients": recipients,
                "date": date_value,
                "raw_email": compressed_email,
            },
        )


# ------------------
# IMAP helpers
# ------------------

def create_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    """
    Create an SSL context; if ca_bundle is provided, load it.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)
    return ctx


async def connect_imap(account: Dict[str, Any]) -> aioimaplib.IMAP4:
    """
    Connect to IMAP using SSL (port 993) or plain (port 143).
    STARTTLS is not implemented because aioimaplib.IMAP4 lacks starttls.
    """
    host = account["host"]
    port = int(account["port"])
    use_ssl = bool(account["use_ssl"])
    require_starttls = bool(account["require_starttls"])
    ca_bundle = account.get("ca_bundle") or None

    if use_ssl:
        # aioimaplib.IMAP4_SSL uses system trust; if you need custom CA,
        # ensure it's in the trust store or patch aioimaplib usage.
        logger.info("Connecting to IMAP via SSL %s:%s", host, port)
        client = aioimaplib.IMAP4_SSL(host, port)
        await client.wait_hello_from_server()
        return client
    else:
        logger.info("Connecting to IMAP (plain) %s:%s", host, port)
        client = aioimaplib.IMAP4(host, port)
        await client.wait_hello_from_server()
        if port == 143 and require_starttls:
            raise RuntimeError("STARTTLS required by config but not supported by aioimaplib.")
        return client


async def fetch_unseen_uids(client: aioimaplib.IMAP4) -> List[str]:
    """
    Search for UNSEEN messages and return a list of UIDs as strings.
    """
    resp = await client.search("UNSEEN")
    uids: List[str] = []

    for line in resp.lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        text_line = line.decode(errors="ignore").strip()
        if not text_line:
            continue
        if "SEARCH" in text_line.upper() and "COMPLETED" in text_line.upper():
            continue
        parts = text_line.split()
        uids.extend(p for p in parts if p.isdigit())

    return uids


def extract_rfc822(resp: Any) -> bytes:
    """
    Extract the RFC822 literal from an aioimaplib FETCH response.

    For your server, the response typically looks like:

        0 b'1 FETCH (FLAGS (\\Seen \\Recent) RFC822 {4976}'
        1 bytearray(b'... full raw email ...')
        2 b')'
        3 b'Fetch completed (...)'

    We:
    - skip non-bytes/bytearray
    - skip metadata lines: starting with '*', 'OK', or exactly ')'
    - return the first remaining bytes/bytearray as the raw email
    """
    for line in resp.lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        if line.startswith(b"*") or line.startswith(b"OK") or line == b")":
            continue
        return bytes(line)

    raise RuntimeError(f"Could not extract RFC822 body from FETCH response: {resp.lines}")


# ------------------
# Per-source processing
# ------------------

async def process_source_messages(source: Dict[str, Any]) -> None:
    client: aioimaplib.IMAP4 | None = None
    account_id = source["id"]
    source_name = source["name"]
    folder = DEFAULT_FOLDER

    try:
        logger.info("Processing source '%s' (id=%s)", source_name, account_id)
        client = await connect_imap(source)

        username = source["username"]
        password_enc = source["password_encrypted"] or ""
        password = decrypt_password(password_enc)

        resp = await client.login(username, password)
        if resp.result != "OK":
            raise RuntimeError(f"IMAP login failed: {resp}")

        resp = await client.select(folder)
        if resp.result != "OK":
            raise RuntimeError(f"IMAP SELECT failed: {resp}")

        uids = await fetch_unseen_uids(client)
        logger.info("Source '%s': %d unseen messages", source_name, len(uids))

        for uid in uids:
            resp = await client.fetch(uid, "(RFC822)")
            if resp.result != "OK":
                raise RuntimeError(f"IMAP FETCH failed for UID {uid}: {resp}")

            raw_email = extract_rfc822(resp)
            insert_message(source, folder, uid, raw_email)

            if source["delete_after_processing"]:
                await client.store(uid, "+FLAGS", "\\Deleted")

        if uids and source["delete_after_processing"]:
            await client.expunge()

        update_heartbeat(account_id, success=True)
        logger.info("Source '%s': processing completed successfully", source_name)

    except Exception as e:
        msg = f"Error processing source '{source_name}': {e}"
        logger.exception(msg)
        update_heartbeat(account_id, success=False, error=str(e))
        log_error(source_name, "Worker error", details=str(e))

    finally:
        if client is not None:
            try:
                await client.logout()
            except Exception:
                pass


# ------------------
# Main loop
# ------------------

async def worker_loop() -> None:
    logger.info("Daygle worker starting up")

    while True:
        cycle_start = datetime.now(timezone.utc)

        sources = load_imap_sources()
        if not sources:
            logger.info("No enabled sources found. Sleeping for 60 seconds.")
            await asyncio.sleep(60)
            continue

        for source in sources:
            poll_interval = int(source.get("poll_interval_seconds") or 300)
            await process_source_messages(source)
            logger.info(
                "Sleeping %d seconds before next poll for source '%s'",
                poll_interval,
                source["name"],
            )
            await asyncio.sleep(poll_interval)

        elapsed = (datetime.now(timezone.utc) - cycle_start).total_seconds()
        logger.info("Worker cycle completed in %.2f seconds", elapsed)


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()