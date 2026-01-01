import os
import asyncio
import ssl
from typing import Dict, Any, List
from datetime import datetime

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from cryptography.fernet import Fernet, InvalidToken
import aioimaplib

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
# Settings / accounts
# ------------------

def load_global_settings() -> dict:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT key, value FROM settings")
        ).mappings().all()
    return {r["key"]: r["value"] for r in rows}


def load_enabled_accounts() -> List[Dict[str, Any]]:
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
    accounts: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        d["password"] = decrypt_password(d["password_encrypted"])
        accounts.append(d)
    return accounts


# ------------------
# Error + heartbeat logging
# ------------------

def log_error(account_id: int, source: str, message: str, details: str | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO error_log (timestamp, source, message, details)
                VALUES (NOW(), :source, :message, :details)
                """
            ),
            {
                "source": source,
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
            {"id": account_id, "msg": message},
        )


def log_success(account_id: int, message: str = "OK") -> None:
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
            {"id": account_id},
        )


def update_heartbeat_only(account_id: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE imap_accounts SET last_heartbeat = NOW() WHERE id = :id"
            ),
            {"id": account_id},
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


async def connect_imap(account: Dict[str, Any]) -> aioimaplib.IMAP4_SSL | aioimaplib.IMAP4:
    host = account["host"]
    port = account["port"]
    use_ssl = account["use_ssl"]
    require_starttls = account["require_starttls"]
    ca_bundle = account["ca_bundle"]

    if use_ssl:
        ctx = create_ssl_context(ca_bundle)
        client = aioimaplib.IMAP4_SSL(host=host, port=port, ssl_context=ctx)
        await client.wait_hello_from_server()
        return client
    else:
        client = aioimaplib.IMAP4(host=host, port=port)
        await client.wait_hello_from_server()
        if port == 143 and require_starttls:
            # STARTTLS support check is limited in aioimaplib;
            # if needed, you can issue CAPABILITY and inspect response.
            ctx = create_ssl_context(ca_bundle)
            await client.starttls(context=ctx)
        return client


# ------------------
# Message processing placeholder
# ------------------

async def process_account_messages(account: Dict[str, Any], storage_dir: str) -> None:
    """
    This is where your existing logic goes:
    - connect to IMAP
    - select folders
    - search for new messages
    - fetch, store to disk under storage_dir
    - insert into messages table
    - delete from server if delete_after_processing
    """
    client = None
    try:
        client = await connect_imap(account)

        # Login
        username = account["username"]
        password = account["password"]
        resp = await client.login(username, password)
        if resp.result != "OK":
            raise RuntimeError(f"IMAP login failed: {resp}")

        # Example: select INBOX
        resp = await client.select("INBOX")
        if resp.result != "OK":
            raise RuntimeError(f"IMAP SELECT failed: {resp}")

        # TODO: adapt your real fetching/UID tracking logic here.
        # For now, we just do a NOOP as a placeholder.
        await client.noop()

        # On success, you’d typically:
        # - write messages to storage_dir
        # - insert/update DB rows
        # That logic is carried over from your current worker.
        # Keep DB access via engine.begin() inside asyncio.to_thread if it gets heavy.

    finally:
        if client is not None:
            try:
                await client.logout()
            except Exception:
                pass


# ------------------
# Per-account async loop
# ------------------

async def account_loop(account: Dict[str, Any], storage_dir: str) -> None:
    account_id = account["id"]
    name = account["name"]
    poll_interval = account["poll_interval_seconds"]

    if poll_interval <= 0:
        poll_interval = 300  # fallback

    while True:
        start = datetime.utcnow()
        try:
            update_heartbeat_only(account_id)
            await process_account_messages(account, storage_dir)
            log_success(account_id)
        except Exception as e:
            msg = f"Worker error for account '{name}': {e}"
            log_error(account_id, source=f"worker:{name}", message=msg)
        # Ensure at least poll_interval between starts, not ends
        elapsed = (datetime.utcnow() - start).total_seconds()
        sleep_for = poll_interval if elapsed >= poll_interval else (poll_interval - elapsed)
        await asyncio.sleep(sleep_for)


# ------------------
# Main loop
# ------------------

async def main():
    global_settings = load_global_settings()
    storage_dir = global_settings.get("storage_dir", "/data/mail")

    # Ensure storage dir exists
    os.makedirs(storage_dir, exist_ok=True)

    # We’ll periodically reload the account list so changes in the DB take effect.
    # For now: load once at startup and spawn one task per enabled account.
    accounts = load_enabled_accounts()
    if not accounts:
        raise RuntimeError("No enabled IMAP accounts found")

    tasks = []
    for account in accounts:
        tasks.append(asyncio.create_task(account_loop(account, storage_dir)))

    # Wait forever on all tasks
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())