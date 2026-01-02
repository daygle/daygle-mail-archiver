import time
import gzip
import email
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

from utils.db import query, execute
from utils.settings import get_retention_config, set_retention_last_run
from security import decrypt_password
from imap_client import ImapConnection

POLL_INTERVAL_FALLBACK = 300  # seconds


# ---------------------------------------------------------
# Error Logging
# ---------------------------------------------------------

def log_error(source: str, message: str, details: str = ""):
    execute(
        """
        INSERT INTO error_log (timestamp, source, message, details)
        VALUES (:ts, :source, :message, :details)
        """,
        {
            "ts": datetime.now(timezone.utc),
            "source": source,
            "message": message[:500],
            "details": details[:4000],
        },
    )


# ---------------------------------------------------------
# IMAP State Helpers
# ---------------------------------------------------------

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


# ---------------------------------------------------------
# Message Storage
# ---------------------------------------------------------

def store_message(source: str, folder: str, uid: int, msg_bytes: bytes):
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


# ---------------------------------------------------------
# Retention Logic
# ---------------------------------------------------------

def compute_retention_cutoff(retention):
    enabled = retention["enabled"] == "true"
    if not enabled:
        return None

    try:
        value = int(retention["value"])
    except ValueError:
        return None

    if value < 1:
        return None

    unit = retention["unit"]
    now = datetime.utcnow()

    if unit == "days":
        return now - timedelta(days=value)
    elif unit == "months":
        return now - relativedelta(months=value)
    elif unit == "years":
        return now - relativedelta(years=value)
    return None


def run_retention_purge_in_worker():
    retention = get_retention_config()
    cutoff = compute_retention_cutoff(retention)

    if cutoff is None:
        return

    row = query(
        """
        SELECT COUNT(*) AS c
        FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    ).mappings().first()

    count = row["c"] if row else 0

    if count == 0:
        set_retention_last_run(datetime.utcnow())
        print(f"[Retention] No messages older than {cutoff}.")
        return

    query(
        """
        DELETE FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    )

    set_retention_last_run(datetime.utcnow())
    print(f"[Retention] Purged {count} message(s) older than {cutoff}.")


# ---------------------------------------------------------
# IMAP Processing
# ---------------------------------------------------------

def process_account(account):
    account_id = account["id"]
    name = account["name"]
    source = name

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

            for mbox in mailboxes:
                parts = mbox.decode().split(" ")
                folder = parts[-1].strip('"')

                conn.select(folder, readonly=True)

                last_uid = get_last_uid(account_id, folder)

                criteria = f"(UID {last_uid+1}:*)" if last_uid > 0 else "ALL"
                status, data = conn.uid("SEARCH", None, criteria)
                if status != "OK" or not data or not data[0]:
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
                    max_uid = max(max_uid, uid)

                if max_uid > last_uid:
                    set_last_uid(account_id, folder, max_uid)

        update_success(account_id)

    except Exception as e:
        msg = f"Error processing account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)


# ---------------------------------------------------------
# Main Loop
# ---------------------------------------------------------

def main_loop():
    while True:
        accounts = get_accounts()
        if not accounts:
            time.sleep(POLL_INTERVAL_FALLBACK)
            continue

        for account in accounts:
            process_account(account)

        # Run retention purge once per cycle
        run_retention_purge_in_worker()

        time.sleep(POLL_INTERVAL_FALLBACK)


if __name__ == "__main__":
    main_loop()
