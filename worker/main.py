import os
import asyncio
import ssl
import logging
from datetime import datetime, UTC
from typing import Any, Dict, List

import aioimaplib
from sqlalchemy import create_engine, text
from mailparser import parse_from_bytes

# ------------------
# Config / globals
# ------------------

DB_DSN = os.getenv("DB_DSN")
STORAGE_DIR_DEFAULT = os.getenv("STORAGE_DIR", "/data/mail")

engine = create_engine(DB_DSN, future=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("daygle-worker")


# ------------------
# DB helpers
# ------------------

def load_global_settings() -> Dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT key, value FROM settings")).mappings().all()
        return {r["key"]: r["value"] for r in rows}


def load_imap_sources() -> List[Dict[str, Any]]:
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


def decrypt_password(token: str) -> str:
    # We don’t have Fernet here; passwords are already decrypted by the API when saving?
    # If your worker needs real decryption, import the same Fernet logic from api/app.py.
    return token  # Adjust if you actually store encrypted passwords for the worker.


def update_heartbeat(account_id: int, success: bool, error: str | None = None) -> None:
    now_sql = "NOW()"
    with engine.begin() as conn:
        if success:
            conn.execute(
                text(
                    f"""
                    UPDATE imap_accounts
                    SET last_heartbeat = {now_sql},
                        last_success = {now_sql},
                        last_error = NULL
                    WHERE id = :id
                    """
                ),
                {"id": account_id},
            )
        else:
            conn.execute(
                text(
                    f"""
                    UPDATE imap_accounts
                    SET last_heartbeat = {now_sql},
                        last_error = :err
                    WHERE id = :id
                    """
                ),
                {"id": account_id, "err": error or "Unknown error"},
            )


def log_error(source_name: str, message: str, details: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO error_log (timestamp, source, message, details)
                VALUES (NOW(), :source, :message, :details)
                """
            ),
            {"source": source_name, "message": message, "details": details or ""},
        )


# ------------------
# IMAP helpers
# ------------------

def create_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)
    return ctx


async def connect_imap(account: Dict[str, Any]) -> aioimaplib.IMAP4:
    host = account["host"]
    port = account["port"]
    use_ssl = account["use_ssl"]
    require_starttls = account["require_starttls"]
    ca_bundle = account.get("ca_bundle") or None

    if use_ssl:
        ctx = create_ssl_context(ca_bundle)
        client = aioimaplib.IMAP4_SSL(host, port)
        await client.wait_hello_from_server()
        # aioimaplib.IMAP4_SSL does not take ssl_context in ctor, but uses system defaults.
        # Your CA bundle is already trusted if system-wide; adjust if needed.
        return client
    else:
        # Plain IMAP (no STARTTLS upgrade here; aioimaplib IMAP4 has no starttls)
        client = aioimaplib.IMAP4(host, port)
        await client.wait_hello_from_server()
        if port == 143 and require_starttls:
            raise RuntimeError("STARTTLS required by config but not supported by aioimaplib.")
        return client


async def fetch_unseen_uids(client: aioimaplib.IMAP4) -> List[str]:
    resp = await client.search("UNSEEN")
    # Typical lines: [b'1 2 3', b'Search completed (...)']
    uids: List[str] = []
    for line in resp.lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        text_line = line.decode().strip()
        if not text_line or "Search completed" in text_line:
            continue
        parts = text_line.split()
        uids.extend(p for p in parts if p.isdigit())
    return uids


# ------------------
# Message processing
# ------------------

def extract_rfc822(resp: Any) -> bytes:
    """
    Extract the RFC822 literal from an aioimaplib FETCH response.
    For your server, the literal is a bytearray on the second line, e.g.:

        0 b'1 FETCH (FLAGS (\\Seen \\Recent) RFC822 {4976}'
        1 bytearray(b'... full raw email ...')
        2 b')'
        3 b'Fetch completed (...)'
    """
    for line in resp.lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        if line.startswith(b"*") or line.startswith(b"OK") or line == b")":
            continue
        return bytes(line)
    raise RuntimeError(f"Could not extract RFC822 body from FETCH response: {resp.lines}")


def get_storage_dir(global_settings: Dict[str, str]) -> str:
    return global_settings.get("storage_dir", STORAGE_DIR_DEFAULT)


def ensure_storage_dir(storage_dir: str) -> None:
    os.makedirs(storage_dir, exist_ok=True)


def save_message_to_disk(raw_email: bytes, storage_dir: str, source_name: str, uid: str) -> str:
    """
    Save the raw email to disk. We’ll store as:
        <storage_dir>/<source_name>/<uid>.eml
    and return the full path for storage in the DB.
    """
    ensure_storage_dir(storage_dir)
    source_dir = os.path.join(storage_dir, source_name)
    os.makedirs(source_dir, exist_ok=True)

    filename = f"{uid}.eml"
    full_path = os.path.join(source_dir, filename)

    with open(full_path, "wb") as f:
        f.write(raw_email)

    return full_path


def insert_message_metadata(
    source: Dict[str, Any],
    folder: str,
    uid: str,
    raw_email: bytes,
    storage_path: str,
) -> None:
    """
    Parse the email and insert a row into messages for the GUI.
    """
    parsed = parse_from_bytes(raw_email)

    subject = parsed.subject or ""
    sender = parsed.from_[0][1] if parsed.from_ else (parsed.from_[0][0] if parsed.from_ else "")
    recipients_list = [addr[1] for addr in (parsed.to or [])]
    recipients = ", ".join(recipients_list)

    # Parsed date; fall back to None
    msg_date = parsed.date  # This is a datetime or None
    if isinstance(msg_date, datetime):
        # Let Postgres handle timestamptz; we’ll pass ISO string
        date_value = msg_date.isoformat()
    else:
        date_value = None

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO messages
                    (source, folder, uid, subject, sender, recipients, date, storage_path, created_at)
                VALUES
                    (:source, :folder, :uid, :subject, :sender, :recipients, :date, :storage_path, NOW())
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
                "storage_path": storage_path,
            },
        )


async def process_source_messages(source: Dict[str, Any], global_settings: Dict[str, str]) -> None:
    client = None
    account_id = source["id"]
    source_name = source["name"]
    storage_dir = get_storage_dir(global_settings)
    folder = "INBOX"  # For now we only process INBOX

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
            storage_path = save_message_to_disk(raw_email, storage_dir, source_name, uid)
            insert_message_metadata(source, folder, uid, raw_email, storage_path)

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
# Main worker loop
# ------------------

async def worker_loop() -> None:
    logger.info("Daygle worker starting up")
    global_settings = load_global_settings()

    while True:
        start = datetime.now(UTC)
        sources = load_imap_sources()
        if not sources:
            logger.info("No enabled sources found. Sleeping for 60 seconds.")
            await asyncio.sleep(60)
            continue

        # Process each source serially; you can make this concurrent later if desired.
        for source in sources:
            poll_interval = int(source.get("poll_interval_seconds") or 300)
            await process_source_messages(source, global_settings)
            # After processing, we sleep per-source if you want staggered polling
            logger.info(
                "Sleeping %d seconds before next poll for source '%s'",
                poll_interval,
                source["name"],
            )
            await asyncio.sleep(poll_interval)

        elapsed = (datetime.now(UTC) - start).total_seconds()
        logger.info("Cycle completed in %.2f seconds", elapsed)


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
    