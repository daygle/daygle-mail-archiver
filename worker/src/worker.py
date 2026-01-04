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
        UPDATE fetch_accounts
        SET last_heartbeat = :ts
        WHERE id = :id
        """,
        {"ts": datetime.now(timezone.utc), "id": account_id},
    )

def update_success(account_id: int):
    execute(
        """
        UPDATE fetch_accounts
        SET last_success = :ts, last_error = NULL
        WHERE id = :id
        """,
        {"ts": datetime.now(timezone.utc), "id": account_id},
    )

def update_error(account_id: int, msg: str):
    execute(
        """
        UPDATE fetch_accounts
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
               delete_after_processing, enabled, account_type
        FROM fetch_accounts
        WHERE enabled = TRUE
        """
    ).mappings().all()
    return rows

def get_last_uid(account_id: int, folder: str) -> int:
    row = query(
        """
        SELECT last_uid
        FROM fetch_state
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
        INSERT INTO fetch_state (account_id, folder, last_uid)
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
    account_type = account.get("account_type", "imap")
    source = name  # used as source label in messages table

    update_heartbeat(account_id)

    try:
        if account_type == "imap":
            process_imap_account(account)
        elif account_type == "gmail":
            process_gmail_account(account)
        elif account_type == "o365":
            process_o365_account(account)
        else:
            raise ValueError(f"Unknown account type: {account_type}")
        
        update_success(account_id)

    except Exception as e:
        msg = f"Error processing account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)


def process_imap_account(account):
    """Process IMAP account"""
    account_id = account["id"]
    name = account["name"]
    source = name

    try:
        password = decrypt_password(account["password_encrypted"])
    except Exception as e:
        msg = f"Failed to decrypt password for account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)
        return

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

        # Iterate over all mailboxes
        for mbox in mailboxes:
            parts = mbox.decode().split(" ")
            folder = parts[-1].strip('"')

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


def process_gmail_account(account):
    """Process Gmail account via API"""
    account_id = account["id"]
    name = account["name"]
    source = name
    folder = "INBOX"  # Gmail uses labels, we'll use INBOX as folder

    # Get valid access token
    access_token = get_valid_token(account_id, "gmail")
    if not access_token:
        raise Exception("Failed to get valid Gmail access token")

    client = GmailClient(access_token)

    # Get last sync token for delta sync
    last_sync_token = get_last_sync_token(account_id, folder)

    # Fetch new message IDs
    message_ids = client.fetch_new_messages(last_sync_token)

    # Process each message
    for msg_id in message_ids:
        try:
            # Get message in raw RFC822 format
            raw_email = client.get_message_raw(msg_id)
            if raw_email:
                # Use message_id hash as UID equivalent
                uid = hash(msg_id) & 0x7FFFFFFF  # Keep as positive int
                store_message(source, folder, uid, raw_email)
        except Exception as e:
            log_error(source, f"Failed to fetch Gmail message {msg_id}: {e}")

    # Update sync token for next run
    new_sync_token = client.get_sync_token()
    if new_sync_token:
        set_last_sync_token(account_id, folder, new_sync_token)


def process_o365_account(account):
    """Process Office 365 account via Graph API"""
    account_id = account["id"]
    name = account["name"]
    source = name
    folder = "INBOX"

    # Get valid access token
    access_token = get_valid_token(account_id, "o365")
    if not access_token:
        raise Exception("Failed to get valid Office 365 access token")

    client = O365Client(access_token)

    # Get last delta link for incremental sync
    last_delta_link = get_last_sync_token(account_id, folder)

    # Fetch new message IDs
    message_ids = client.fetch_new_messages(last_delta_link)

    # Process each message
    for msg_id in message_ids:
        try:
            # Get message in MIME format
            raw_email = client.get_message_mime(msg_id)
            if raw_email:
                # Use message_id hash as UID equivalent
                uid = hash(msg_id) & 0x7FFFFFFF  # Keep as positive int
                store_message(source, folder, uid, raw_email)
        except Exception as e:
            log_error(source, f"Failed to fetch O365 message {msg_id}: {e}")

    # Update delta link for next run
    new_delta_link = client.get_delta_link()
    if new_delta_link:
        set_last_sync_token(account_id, folder, new_delta_link)


def get_last_sync_token(account_id: int, folder: str) -> str:
    """Get last sync token (for Gmail/O365 delta sync)"""
    row = query(
        """
        SELECT last_sync_token
        FROM fetch_state
        WHERE account_id = :id AND folder = :folder
        """,
        {"id": account_id, "folder": folder},
    ).mappings().first()

    if row and row["last_sync_token"]:
        return row["last_sync_token"]
    return None


def set_last_sync_token(account_id: int, folder: str, token: str):
    """Store sync token for next delta sync"""
    execute(
        """
        INSERT INTO fetch_state (account_id, folder, last_sync_token)
        VALUES (:id, :folder, :token)
        ON CONFLICT (account_id, folder)
        DO UPDATE SET last_sync_token = EXCLUDED.last_sync_token
        """,
        {"id": account_id, "folder": folder, "token": token},
    )

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