import os
import asyncio
import ssl
from typing import Dict, Any, List
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet, InvalidToken
import aioimaplib

# ------------------
# Env + DB setup
# ------------------

load_dotenv()

DB_DSN = os.getenv("DB_DSN")
IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")

if not DB_DSN:
    raise RuntimeError("DB_DSN is not set")

engine = create_engine(DB_DSN, future=True)


# ------------------
# Encryption helpers
# ------------------

def get_fernet() -> Fernet | None:
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


# ------------------
# Settings / sources
# ------------------

def load_global_settings() -> dict:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT key, value FROM settings")
        ).mappings().all()
    return {r["key"]: r["value"] for r in rows}


def load_enabled_sources() -> List[Dict[str, Any]]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, host, port, username, password_encrypted,
                       use_ssl, require_starttls, ca_bundle,
                       poll_interval_seconds, delete_after_processing,
                       enabled
                FROM imap_accounts
                WHERE enabled = TRUE
                ORDER BY name
                """
            )
        ).mappings().all()

    sources: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["password"] = decrypt_password(d["password_encrypted"])
        sources.append(d)
    return sources


# ------------------
# Error + heartbeat logging
# ------------------

def log_error(source_id: int, source_name: str, message: str, details: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO error_log (timestamp, source, message, details)
                VALUES (NOW(), :source, :message, :details)
                """
            ),
            {
                "source": f"source:{source_name}",
                "message": message,
                "details": details or "",
            },
        )
        conn.execute(
            text(
                """
                UPDATE imap_accounts
                SET last_error = :msg, last_heartbeat = NOW()
                WHERE id = :id
                """
            ),
            {"id": source_id, "msg": message},
        )


def log_success(source_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE imap_accounts
                SET last_success = NOW(),
                    last_error = NULL,
                    last_heartbeat = NOW()
                WHERE id = :id
                """
            ),
            {"id": source_id},
        )


def update_heartbeat_only(source_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE imap_accounts SET last_heartbeat = NOW() WHERE id = :id"
            ),
            {"id": source_id},
        )


# ------------------
# IMAP helpers (async)
# ------------------

def create_ssl_context(ca_bundle: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    if ca_bundle:
        ctx.load_verify_locations(ca_bundle)
    return ctx


async def connect_imap(source: Dict[str, Any]) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
    host = source["host"]
    port = source["port"]
    use_ssl = source["use_ssl"]
    require_starttls = source["require_starttls"]
    ca_bundle = source["ca_bundle"]

    # NOTE:
    # aioimaplib.IMAP4 does not implement .starttls(), so we treat both SSL and STARTTLS
    # as "encrypted over TLS" and use IMAP4_SSL in both cases. Plain IMAP only if neither is set.
    if use_ssl or require_starttls:
        ctx = create_ssl_context(ca_bundle)
        client = aioimaplib.IMAP4_SSL(host=host, port=port, ssl_context=ctx)
        await client.wait_hello_from_server()
        return client

    client = aioimaplib.IMAP4(host=host, port=port)
    await client.wait_hello_from_server()
    return client


async def fetch_unseen_uids(client) -> List[str]:
    resp = await client.search("UNSEEN")
    if resp.result != "OK":
        raise RuntimeError(f"IMAP SEARCH failed: {resp}")

    if not resp.lines:
        return []

    line = resp.lines[0].decode().strip()
    if not line:
        return []

    return line.split()


# ------------------
# Message storage helpers
# ------------------

def save_message_to_disk(raw_bytes: bytes, storage_dir: str, uid: str) -> None:
    os.makedirs(storage_dir, exist_ok=True)
    path = os.path.join(storage_dir, f"{uid}.eml")
    with open(path, "wb") as f:
        f.write(raw_bytes)


# ------------------
# Message processing
# ------------------

def extract_rfc822(resp):
    """
    Extract the RFC822 literal from an aioimaplib FETCH response.
    We scan for the first line that looks like raw email bytes and
    skip metadata / status lines.
    """
    for line in resp.lines:
        if not isinstance(line, bytes):
            continue
        # Skip metadata / structural lines
        if line.startswith(b"*") and b"FETCH" in line:
            continue
        if line.strip() == b")":
            continue
        if line.startswith(b"OK "):
            continue

        # First remaining bytes line is treated as the raw message
        return line

    raise RuntimeError(f"Could not extract RFC822 body from FETCH response: {resp.lines}")


async def process_source_messages(source: Dict[str, Any], storage_dir: str) -> None:
    client = None
    try:
        client = await connect_imap(source)

        username = source["username"]
        password = source["password"]

        resp = await client.login(username, password)
        if resp.result != "OK":
            raise RuntimeError(f"IMAP login failed: {resp}")

        resp = await client.select("INBOX")
        if resp.result != "OK":
            raise RuntimeError(f"IMAP SELECT failed: {resp}")

        uids = await fetch_unseen_uids(client)

        for uid in uids:
            resp = await client.fetch(uid, "(RFC822)")
            if resp.result != "OK":
                raise RuntimeError(f"IMAP FETCH failed for UID {uid}: {resp}")

            raw_email = extract_rfc822(resp)
            save_message_to_disk(raw_email, storage_dir, uid)

            if source["delete_after_processing"]:
                await client.store(uid, "+FLAGS", "\\Deleted")

        if source["delete_after_processing"]:
            await client.expunge()

    finally:
        if client is not None:
            try:
                await client.logout()
            except Exception:
                pass


# ------------------
# Per-source async loop
# ------------------

async def source_loop(source: Dict[str, Any], storage_dir: str) -> None:
    source_id = source["id"]
    name = source["name"]
    poll_interval = source["poll_interval_seconds"]

    if poll_interval <= 0:
        poll_interval = 300

    while True:
        start = datetime.utcnow()
        try:
            update_heartbeat_only(source_id)
            await process_source_messages(source, storage_dir)
            log_success(source_id)
        except Exception as e:
            msg = f"Worker error for source '{name}': {e}"
            log_error(source_id, name, msg)

        elapsed = (datetime.utcnow() - start).total_seconds()
        sleep_for = poll_interval if elapsed >= poll_interval else (poll_interval - elapsed)
        await asyncio.sleep(sleep_for)


# ------------------
# Main loop
# ------------------

async def main():
    global_settings = load_global_settings()
    storage_dir = global_settings.get("storage_dir", "/data/mail")

    os.makedirs(storage_dir, exist_ok=True)

    sources = load_enabled_sources()
    if not sources:
        raise RuntimeError("No enabled sources found")

    tasks = []
    for source in sources:
        tasks.append(asyncio.create_task(source_loop(source, storage_dir)))

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())