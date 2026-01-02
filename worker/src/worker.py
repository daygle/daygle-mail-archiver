import time
import gzip
import email
from datetime import datetime, timezone, timedelta

from db import query, execute
from security import decrypt_password
from imap_client import ImapConnection

POLL_INTERVAL_FALLBACK = 300  # seconds

def log_error(source: str, message: str, details: str = "", level: str = "error"):
    execute(
        """
        INSERT INTO logs (timestamp, level, source, message, details)
        VALUES (:ts, :level, :source, :message, :details)
        """,
        {
            "ts": datetime.now(timezone.utc),
            "level": level,
            "source": source,
            "message": message[:500],
            "details": details[:4000],
        },
    )

def update_heartbeat(account_id: int):
    execute(
        """
        UPDATE imap_accounts
        SET last_heartbeat = :ts
        WHERE id = :id
        """,
        {"ts": datetime.now(timezone.utc), "id": account_id},
    )

def update_success(account_id: int):
    execute(
        """
        UPDATE imap_accounts
        SET last_success = :ts, last_error = NULL
        WHERE id = :id
        """,
        {"ts": datetime.now(timezone.utc), "id": account_id},
    )

def update_error(account_id: int, msg: str):
    execute(
        """
        UPDATE imap_accounts
        SET last_error = :msg
        WHERE id = :id
        """,
        {"msg": msg[:500], "id": account_id},
    )

def get_accounts():
    rows = query(
        """
        SELECT id, name, host, port, username, password_encrypted,
               use_ssl, require_starttls, poll_interval_seconds,
               delete_after_processing, enabled
        FROM imap_accounts
        WHERE enabled = TRUE
        """
    ).mappings().all()
    return rows

def get_last_uid(account_id: int, folder: str) -> int:
    row = query(
        """
        SELECT last_uid
        FROM imap_state
        WHERE account_id = :id AND folder = :folder
        """,
        {"id": account_id, "folder": folder},
    ).mappings().first()

    if row and row["last_uid"] is not None:
        return int(row["last_uid"])
    return 0

def set_last_uid(account_id: int, folder: str, uid: int):
    execute(
        """
        INSERT INTO imap_state (account_id, folder, last_uid)
        VALUES (:id, :folder, :uid)
        ON CONFLICT (account_id, folder)
        DO UPDATE SET last_uid = EXCLUDED.last_uid
        """,
        {"id": account_id, "folder": folder, "uid": uid},
    )

def store_message(
    source: str,
    folder: str,
    uid: int,
    msg_bytes: bytes,
):
    compressed_bytes = gzip.compress(msg_bytes)

    msg = email.message_from_bytes(msg_bytes)
    subject = msg.get("Subject", "")
    sender = msg.get("From", "")
    recipients = ", ".join(
        filter(None, [msg.get("To"), msg.get("Cc"), msg.get("Bcc")])
    )
    date_header = msg.get("Date", "")

    execute(
        """
        INSERT INTO messages
        (source, folder, uid, subject, sender, recipients, date,
         raw_email, compressed)
        VALUES
        (:source, :folder, :uid, :subject, :sender, :recipients, :date,
         :raw_email, TRUE)
        ON CONFLICT (source, folder, uid) DO NOTHING
        """,
        {
            "source": source,
            "folder": folder,
            "uid": uid,
            "subject": subject,
            "sender": sender,
            "recipients": recipients,
            "date": date_header,
            "raw_email": compressed_bytes,
        },
    )

def process_account(account):
    account_id = account["id"]
    name = account["name"]
    source = name  # used as source label in messages table

    update_heartbeat(account_id)

    try:
        password = decrypt_password(account["password_encrypted"])
    except Exception as e:
        msg = f"Failed to decrypt password for account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)
        return

    try:
        with ImapConnection(
            host=account["host"],
            port=account["port"],
            username=account["username"],
            password=password,
            use_ssl=account["use_ssl"],
            require_starttls=account["require_starttls"],
        ) as conn:

            status, mailboxes = conn.list()
            if status != "OK":
                raise RuntimeError(f"LIST failed: {status}")

            # Simple approach: iterate over all mailboxes
            for mbox in mailboxes:
                parts = mbox.decode().split(" ")
                folder = parts[-1].strip('"')

                # Optional: skip special folders if desired
                # Here we just process everything.
                conn.select(folder, readonly=True)

                last_uid = get_last_uid(account_id, folder)

                # UID search: all messages with UID greater than last_uid
                if last_uid > 0:
                    criteria = f"(UID {last_uid+1}:*)"
                else:
                    criteria = "ALL"

                status, data = conn.uid("SEARCH", None, criteria)
                if status != "OK":
                    continue

                if not data or not data[0]:
                    continue

                uids = [int(u) for u in data[0].split()]
                max_uid = last_uid

                for uid in uids:
                    if uid <= last_uid:
                        continue

                    status, msg_data = conn.uid("FETCH", str(uid), "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw = msg_data[0][1]
                    store_message(source, folder, uid, raw)
                    if uid > max_uid:
                        max_uid = uid

                if max_uid > last_uid:
                    set_last_uid(account_id, folder, max_uid)

        update_success(account_id)

    except Exception as e:
        msg = f"Error processing account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)

def get_settings():
    rows = query("SELECT key, value FROM settings").mappings().all()
    return {r["key"]: r["value"] for r in rows}

def purge_old_messages():
    settings = get_settings()
    enable_purge = settings.get("enable_purge", "false").lower() == "true"
    if not enable_purge:
        return

    retention_value = int(settings.get("retention_value", 1))
    retention_unit = settings.get("retention_unit", "years")

    now = datetime.now(timezone.utc)
    if retention_unit == "days":
        cutoff = now - timedelta(days=retention_value)
    elif retention_unit == "months":
        cutoff = now - timedelta(days=retention_value * 30)  # Approximate
    elif retention_unit == "years":
        cutoff = now - timedelta(days=retention_value * 365)  # Approximate
    else:
        return  # Invalid unit

    execute(
        """
        DELETE FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    )

def main_loop():
    while True:
        accounts = get_accounts()
        if not accounts:
            time.sleep(POLL_INTERVAL_FALLBACK)
            continue

        for account in accounts:
            poll_interval = account["poll_interval_seconds"] or POLL_INTERVAL_FALLBACK
            process_account(account)
            # No per-account sleep here; we do a global sleep after all accounts

        # Purge old messages after processing all accounts
        purge_old_messages()

        # Sleep before next cycle
        time.sleep(POLL_INTERVAL_FALLBACK)

if __name__ == "__main__":
    main_loop()