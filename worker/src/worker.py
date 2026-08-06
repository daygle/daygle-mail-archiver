import sys
import time
import gzip
import email
import hashlib
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from collections import defaultdict

from sqlalchemy.exc import IntegrityError

from db import query, execute
from security import decrypt_password
from imap_client import ImapConnection
from gmail_client import GmailClient
from o365_client import O365Client
from clamav_scanner import ClamAVScanner
from utils.email_parser import decode_header, compute_signature

POLL_INTERVAL_FALLBACK = 300  # seconds

# PostgreSQL INTEGER column maximum is 2^31 - 1; synthetic UIDs stay below it.
_MAX_SYNTHETIC_UID = 2_147_483_646

# Retention purge drains the archive in batches so a single cycle never loads
# the whole (potentially very large) table into memory.
PURGE_BATCH_SIZE = 1000


def stable_uid(email_id: str) -> int:
    """Derive a stable, non-negative synthetic UID from a provider message id.

    Gmail/O365 identify messages by opaque string ids, but the emails table keys
    on an INTEGER `uid`. Python's built-in hash() is salted per process
    (PYTHONHASHSEED), so it produces different values across worker restarts,
    causing the same message to be re-stored under a new uid instead of being
    deduplicated by ON CONFLICT (source, folder, uid). Use SHA-256 so the mapping
    is deterministic. The result is kept below 10**9 to fit a PostgreSQL INTEGER
    column (max 2,147,483,647) and to preserve the previous value range.
    """
    digest = hashlib.sha256(email_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (10**9)


def resolve_provider_uid(source: str, folder: str, provider_id: str) -> int:
    """Resolve a stable, collision-free synthetic UID for a provider message id.

    ``stable_uid`` derives a 30-bit hash which *can* collide once an archive
    grows past ~50k messages; a collision would make two distinct Gmail/O365
    messages share (source, folder, uid), and the emails table's ON CONFLICT
    upsert would silently overwrite one of them. To prevent that, every provider
    message claims its uid in ``email_uid_aliases``. If the primary hash is
    already taken by a different message, the next free uid is used and the
    mapping is persisted so later runs stay stable.

    Raises RuntimeError if no free uid can be found (pathological) and lets
    transient database errors propagate so the caller retries the batch.
    """
    primary = stable_uid(provider_id)

    # Claim the primary uid for this provider message. ON CONFLICT on the primary
    # key makes re-claims idempotent; a UNIQUE (source, folder, uid) conflict (the
    # primary is owned by a *different* message) raises and falls through to the scan.
    try:
        execute(
            """
            INSERT INTO email_uid_aliases (source, folder, provider_id, uid)
            VALUES (:source, :folder, :provider_id, :uid)
            ON CONFLICT (source, folder, provider_id) DO NOTHING
            """,
            {"source": source, "folder": folder, "provider_id": provider_id, "uid": primary},
        )
    except IntegrityError:
        # primary uid taken by a different message; scan for a free one below
        pass

    row = query(
        """
        SELECT uid FROM email_uid_aliases
        WHERE source = :source AND folder = :folder AND provider_id = :provider_id
        """,
        {"source": source, "folder": folder, "provider_id": provider_id},
    ).mappings().first()
    if row:
        return int(row["uid"])

    # The primary was taken by another message (or the claim raced with another
    # worker). Scan forward for the next free uid and remember the mapping.
    uid = primary
    for _ in range(1_000_000):
        uid = uid + 1 if uid < _MAX_SYNTHETIC_UID else 1
        if uid == primary:
            break
        try:
            execute(
                """
                INSERT INTO email_uid_aliases (source, folder, provider_id, uid)
                VALUES (:source, :folder, :provider_id, :uid)
                """,
                {"source": source, "folder": folder, "provider_id": provider_id, "uid": uid},
            )
            log_error(
                source,
                f"UID collision resolved for provider message {provider_id}: {primary} -> {uid}",
                level="warning",
            )
            return uid
        except IntegrityError:
            continue  # taken by another message; keep scanning

    raise RuntimeError(f"Could not allocate a synthetic UID for provider message {provider_id}")


def _quote_imap_folder(folder: str) -> str:
    """Quote an IMAP mailbox name when it needs escaping for the protocol."""
    if any(ch in folder for ch in (' ', '"', "\\", "\t")):
        return '"' + folder.replace("\\", "\\\\").replace('"', r"\"") + '"'
    return folder


def _ensure_worker_schema():
    """Create tables the worker needs that may be missing on pre-update databases.

    db/schema.sql is the source of truth and is re-applied by update.sh, but
    running this here keeps an upgraded-but-not-yet-migrated deployment working.
    """
    try:
        execute(
            """
            CREATE TABLE IF NOT EXISTS email_uid_aliases (
                source TEXT NOT NULL,
                folder TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                uid INTEGER NOT NULL,
                PRIMARY KEY (source, folder, provider_id),
                UNIQUE (source, folder, uid)
            )
            """
        )
    except Exception as e:
        log_error("Worker", f"Failed to ensure worker schema: {e}")


def _parse_quoted_imap_token(value: str):
    if not value or value[0] != '"':
        return None, 0
    out = []
    i = 1
    while i < len(value):
        ch = value[i]
        if ch == "\\":
            if i + 1 >= len(value):
                return None, 0
            out.append(value[i + 1])
            i += 2
            continue
        if ch == '"':
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    return None, 0


def _parse_list_mailbox(mbox):
    mbox_str = (
        mbox.decode("utf-8", errors="replace")
        if isinstance(mbox, (bytes, bytearray))
        else str(mbox)
    )
    mbox_str = mbox_str.strip()
    if not mbox_str.startswith("("):
        return None

    flags_end = mbox_str.find(")")
    if flags_end <= 0:
        return None
    flags_str = mbox_str[1:flags_end]
    remainder = mbox_str[flags_end + 1 :].lstrip()
    if not remainder:
        return None

    if remainder.startswith("NIL"):
        remainder = remainder[3:].lstrip()
    elif remainder.startswith('"'):
        _, consumed = _parse_quoted_imap_token(remainder)
        if consumed == 0:
            return None
        remainder = remainder[consumed:].lstrip()
    else:
        return None

    if not remainder:
        return None

    folder = remainder.strip()
    if folder.startswith('"'):
        parsed_folder, consumed = _parse_quoted_imap_token(folder)
        if consumed == 0 or folder[consumed:].strip():
            return None
        folder = parsed_folder

    return {f.lower() for f in flags_str.split()}, folder

# Initialise ClamAV scanner (singleton)
clamav_scanner = None

def get_clamav_scanner():
    """Get or initialise the ClamAV scanner."""
    global clamav_scanner
    if clamav_scanner is None:
        clamav_scanner = ClamAVScanner()
    return clamav_scanner

def log_error(source: str, message: str, details: str = "", level: str = "error"):
    """Log an entry to the database.

    Never raises: logging is best-effort and must not itself take the worker
    down (it is frequently called *because* the database is unavailable). On
    failure the message falls back to stderr so it still reaches container logs.
    """
    try:
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
    except Exception:
        try:
            print(f"[{level.upper()}] {source}: {message}", file=sys.stderr)
        except Exception:
            pass


def create_alert(alert_type: str, title: str, message: str, details: str = None, trigger_key: str = None):
    """
    Create a system alert (worker-side implementation).
    
    Args:
        alert_type: Type of alert ('error', 'warning', 'info', 'success') - can be overridden by trigger_key
        title: Alert title
        message: Alert message
        details: Optional detailed information
        trigger_key: Optional trigger key to check if alert should be created and get severity from
    """
    # If trigger_key is provided, look up the configured alert_type and check if enabled
    actual_alert_type = alert_type
    if trigger_key:
        try:
            result = query("SELECT alert_type, enabled FROM alert_triggers WHERE trigger_key = :key", {"key": trigger_key}).mappings().first()
            if result:
                if not result["enabled"]:
                    # Trigger is disabled, don't create alert
                    return
                # Use the configured alert_type from the database
                actual_alert_type = result["alert_type"]
        except Exception:
            # If we can't check the trigger, use the provided alert_type
            pass
    
    try:
        execute("""
            INSERT INTO alerts (alert_type, title, message, details)
            VALUES (:alert_type, :title, :message, :details)
        """, {
            "alert_type": actual_alert_type,
            "title": title,
            "message": message,
            "details": details
        })
    except Exception as e:
        # If alert creation fails, just log it - don't break email processing
        log_error("Alert", f"Failed to create alert '{title}': {str(e)}", level="warning")

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
               delete_after_processing, expunge_deleted, enabled, account_type
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

def store_email(
    source: str,
    folder: str,
    uid: int,
    email_bytes: bytes,
) -> bool:
    """
    Store email in the database with virus scanning.

    Args:
        source: Email source/account name
        folder: Email folder
        uid: Email UID
        email_bytes: Raw email content

    Returns:
        True if email was stored, False if rejected due to virus
    """
    # Parse email once for efficiency
    msg = email.message_from_bytes(email_bytes)
    subject = decode_header(msg.get("Subject", ""))
    sender_raw = msg.get("From")
    sender = decode_header(sender_raw) if sender_raw is not None else None
    # Combine To/Cc into recipients string
    recipients_list = []
    for h in ("To", "Cc"):
        vals = msg.get_all(h, [])
        if vals:
            recipients_list.extend(decode_header(v) for v in vals)
    recipients = ", ".join(recipients_list) if recipients_list else None
    date_header = msg.get("Date")
    # Parse the RFC822 date string into a timezone-aware datetime for reliable sorting
    date_parsed = None
    if date_header:
        try:
            date_parsed = parsedate_to_datetime(date_header)
        except (ValueError, TypeError):
            date_parsed = None
    message_id_raw = msg.get("Message-ID")
    message_id = decode_header(message_id_raw) if message_id_raw is not None else None

    # Compress the raw email for storage. If compression ever fails, store the
    # original bytes uncompressed instead of losing the raw email entirely.
    try:
        compressed_bytes = gzip.compress(email_bytes)
        compressed_flag = True
    except Exception:
        compressed_bytes = None
        compressed_flag = False

    # Default flags
    virus_scanned = False
    virus_detected = False
    virus_name = None
    scan_timestamp = None
    quarantined = False

    scanner = get_clamav_scanner()
    if scanner.requires_scan():
        virus_detected, virus_name, scan_timestamp, virus_scanned = scanner.scan(email_bytes)
        if not virus_scanned:
            # Do not silently archive an email when ClamAV is enabled but the
            # scan could not complete. Raising preserves the provider cursor so
            # the message is retried after ClamAV recovers.
            raise RuntimeError("ClamAV scan did not complete; email was not archived")

        if virus_detected:
            action = scanner.get_action()
            log_error(
                source,
                f"Virus detected in email: {virus_name}",
                f"Subject: {subject or 'N/A'}, UID: {uid}, Folder: {folder}, Action: {action}",
                level="warning",
            )

            alert_details = f"""Virus: {virus_name}
Subject: {subject or 'N/A'}
From: {sender or 'Unknown'}
Account: {source}
Folder: {folder}
UID: {uid}
Action Taken: {action}"""

            create_alert(
                'error',
                'Virus Detected in Email',
                f'Malicious email detected: {virus_name}',
                alert_details,
                'virus_detected',
            )

            if action == 'reject':
                log_error(
                    source,
                    f"Email rejected due to virus: {virus_name}",
                    f"UID: {uid}, Folder: {folder}",
                    level="info",
                )
                return False

            if action == 'quarantine' and scanner._quarantine_in_db:
                # Store the (optionally encrypted) bytes in quarantined_emails
                try:
                    raw_to_store = compressed_bytes if compressed_flag else email_bytes
                    if scanner._quarantine_encrypt and scanner._quarantine_key and raw_to_store:
                        try:
                            raw_to_store = scanner._quarantine_key.encrypt(raw_to_store)
                        except Exception as e:
                            log_error(source, f"Failed to encrypt quarantined data: {e}")
                            # fall through to store unencrypted if encryption fails
                    expires_at = None
                    try:
                        expires_at = datetime.now(timezone.utc) + timedelta(days=int(scanner._quarantine_retention_days))
                    except Exception:
                        expires_at = None

                    try:
                        sig = compute_signature(email_bytes)
                    except Exception:
                        sig = None

                    execute(
                        """
                        INSERT INTO quarantined_emails
                        (original_source, original_folder, original_uid, subject, sender, recipients, date, date_parsed, message_id, raw_email, signature, compressed, virus_name, virus_scanned, virus_detected, scan_timestamp, reason, quarantined_at, expires_at, quarantined_by)
                        VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :date_parsed, :message_id, :raw_email, :signature, :compressed, :virus_name, TRUE, TRUE, :scan_timestamp, :reason, NOW(), :expires_at, :quarantined_by)
                        ON CONFLICT DO NOTHING
                        """,
                        {
                            "source": source,
                            "folder": folder,
                            "uid": uid,
                            "subject": subject,
                            "sender": sender,
                            "recipients": recipients,
                            "date": date_header,
                            "date_parsed": date_parsed,
                            "message_id": message_id,
                            "raw_email": raw_to_store,
                            "signature": sig,
                            "compressed": compressed_flag,
                            "virus_name": virus_name,
                            "scan_timestamp": scan_timestamp,
                            "reason": 'quarantined by ClamAV',
                            "expires_at": expires_at,
                            "quarantined_by": 'clamav',
                        },
                    )
                    quarantined = True
                    # Avoid saving raw email in main table for quarantined messages
                    compressed_bytes = None
                except Exception as e:
                    log_error(source, f"Failed to quarantine email: {e}")
                    # Do not advance fetch state after a quarantine DB failure;
                    # re-raise so the message is retried on the next poll instead
                    # of being silently lost.
                    raise

    # Insert into emails table (store compressed bytes unless quarantined)
    if not quarantined:
        try:
            try:
                sig = compute_signature(email_bytes)
            except Exception:
                sig = None

            execute(
                """
                INSERT INTO emails (source, folder, uid, subject, sender, recipients, date, date_parsed, message_id, raw_email, signature, compressed, virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined)
                VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :date_parsed, :message_id, :raw_email, :signature, :compressed, :virus_scanned, :virus_detected, :virus_name, :scan_timestamp, :quarantined)
                ON CONFLICT (source, folder, uid) DO UPDATE SET
                    subject = EXCLUDED.subject,
                    sender = EXCLUDED.sender,
                    recipients = EXCLUDED.recipients,
                    date = EXCLUDED.date,
                    date_parsed = EXCLUDED.date_parsed,
                    message_id = EXCLUDED.message_id,
                    raw_email = EXCLUDED.raw_email,
                    signature = COALESCE(EXCLUDED.signature, emails.signature),
                    compressed = EXCLUDED.compressed,
                    -- Never erase a completed scan if a retry/re-fetch has no
                    -- fresh result (for example while scanning is disabled).
                    virus_scanned = CASE
                        WHEN EXCLUDED.virus_scanned THEN TRUE
                        WHEN emails.signature IS NOT NULL
                             AND EXCLUDED.signature IS NOT NULL
                             AND emails.signature = EXCLUDED.signature
                            THEN emails.virus_scanned
                        ELSE FALSE
                    END,
                    virus_detected = CASE
                        WHEN EXCLUDED.virus_scanned THEN EXCLUDED.virus_detected
                        WHEN emails.signature IS NOT NULL
                             AND EXCLUDED.signature IS NOT NULL
                             AND emails.signature = EXCLUDED.signature
                            THEN emails.virus_detected
                        ELSE FALSE
                    END,
                    virus_name = CASE
                        WHEN EXCLUDED.virus_scanned THEN EXCLUDED.virus_name
                        WHEN emails.signature IS NOT NULL
                             AND EXCLUDED.signature IS NOT NULL
                             AND emails.signature = EXCLUDED.signature
                            THEN emails.virus_name
                        ELSE NULL
                    END,
                    scan_timestamp = CASE
                        WHEN EXCLUDED.virus_scanned THEN EXCLUDED.scan_timestamp
                        WHEN emails.signature IS NOT NULL
                             AND EXCLUDED.signature IS NOT NULL
                             AND emails.signature = EXCLUDED.signature
                            THEN emails.scan_timestamp
                        ELSE NULL
                    END,
                    quarantined = EXCLUDED.quarantined
                """,
                {
                    "source": source,
                    "folder": folder,
                    "uid": uid,
                    "subject": subject,
                    "sender": sender,
                    "recipients": recipients,
                    "date": date_header,
                    "date_parsed": date_parsed,
                    "message_id": message_id,
                    "raw_email": compressed_bytes if compressed_flag else email_bytes,
                    "signature": sig,
                    "compressed": compressed_flag,
                    "virus_scanned": virus_scanned,
                    "virus_detected": virus_detected,
                    "virus_name": virus_name,
                    "scan_timestamp": scan_timestamp,
                    "quarantined": quarantined,
                },
            )
        except Exception as e:
            log_error(source, f"Failed to store email in database: {e}")
            # Re-raise so callers do NOT advance last_uid / the sync token; the
            # email is retried on the next poll instead of being skipped forever.
            raise

    return True

def process_account(account):
    account_id = account["id"]
    name = account["name"]
    account_type = account.get("account_type", "imap")
    source = name  # used as source label in emails table

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
    delete_after_processing = account.get("delete_after_processing", False)
    expunge_deleted = account.get("expunge_deleted", False)

    try:
        password = decrypt_password(account["password_encrypted"])
    except Exception as e:
        msg = f"Failed to decrypt password for account {account_id}: {e}"
        log_error(source, msg)
        update_error(account_id, msg)
        return
    # Wrap IMAP connection and mailbox operations in a retry loop to tolerate
    # transient network/DNS issues (e.g. during server reboot).
    import socket
    import imaplib

    max_attempts = 5
    backoff = 1
    for attempt in range(1, max_attempts + 1):
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

                # Iterate over all mailboxes
                for mbox in mailboxes:
                    if mbox is None:
                        continue
                    parsed = _parse_list_mailbox(mbox)
                    if not parsed:
                        continue

                    flag_tokens, folder = parsed
                    if "\\noselect" in flag_tokens or "\\nonexistent" in flag_tokens:
                        continue

                    folder_for_imap = _quote_imap_folder(folder)

                    # Select folder as readonly unless we need to delete
                    try:
                        status_sel, _ = conn.select(folder_for_imap, readonly=not delete_after_processing)
                    except imaplib.IMAP4.error:
                        continue
                    if status_sel != "OK":
                        continue

                    last_uid = get_last_uid(account_id, folder)

                    # UID search: all emails with UID greater than last_uid
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

                        status, email_data = conn.uid("FETCH", str(uid), "(RFC822)")
                        if status != "OK" or not email_data or not email_data[0]:
                            continue

                        raw = email_data[0][1]
                        store_email(source, folder, uid, raw)
                        
                        # Delete from server if configured
                        if delete_after_processing:
                            try:
                                # Mark email as deleted (IMAP standard)
                                # If expunge is disabled, email stays flagged but visible in mail clients
                                # If expunge is enabled, email is permanently removed
                                conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                            except Exception as e:
                                log_error(source, f"Failed to mark UID {uid} as deleted in folder {folder}: {e}")
                        
                        if uid > max_uid:
                            max_uid = uid
                    
                    # Expunge deleted emails only if expunge flag is enabled
                    if delete_after_processing and expunge_deleted:
                        try:
                            conn.expunge()
                        except Exception as e:
                            log_error(source, f"Failed to expunge folder {folder}: {e}")

                    if max_uid > last_uid:
                        set_last_uid(account_id, folder, max_uid)

            # success, break out of attempt loop
            break
        except Exception as e:
            # Treat common network/name resolution errors as transient
            transient = isinstance(e, (OSError, socket.gaierror, socket.timeout, imaplib.IMAP4.abort))
            msgstr = str(e)
            if not transient:
                if "Network is unreachable" in msgstr or "Temporary failure in name resolution" in msgstr or "timed out" in msgstr:
                    transient = True

            if transient:
                log_error(source, f"Network error processing IMAP account {account_id} (attempt {attempt}/{max_attempts}): {e}", level="warning")
                if attempt == max_attempts:
                    # re-raise to be handled by outer process_account
                    raise
                time.sleep(backoff)
                backoff *= 2
                continue
            else:
                # Non-transient error; re-raise to be handled upstream
                raise


def process_gmail_account(account):
    """Process Gmail account via API"""
    account_id = account["id"]
    name = account["name"]
    source = name
    folder = "INBOX"  # Gmail uses labels, we'll use INBOX as folder
    delete_after_processing = account.get("delete_after_processing", False)

    # Get valid access token
    access_token = get_valid_token(account_id, "gmail")
    if not access_token:
        raise Exception("Failed to get valid Gmail access token")

    client = GmailClient(access_token)

    # Get last sync token for delta sync
    last_sync_token = get_last_sync_token(account_id, folder)

    # Fetch new email IDs
    email_ids = client.fetch_new_emails(last_sync_token)

    # Process each email. Any failure (network, provider, or database) must keep
    # the sync token unchanged so the failed message is retried on the next run
    # instead of being permanently skipped by the delta.
    had_error = False
    for email_id in email_ids:
        try:
            # Get email in raw RFC822 format
            raw_email = client.get_message_raw(email_id)
            if not raw_email:
                had_error = True
                log_error(source, f"Empty raw response for Gmail email {email_id}")
                break

            # Collision-safe stable uid for this provider message
            uid = resolve_provider_uid(source, folder, email_id)
            store_email(source, folder, uid, raw_email)

            # Delete from Gmail (move to trash) if configured
            if delete_after_processing:
                if not client.delete_message(email_id):
                    log_error(source, f"Failed to delete Gmail email {email_id}")
        except Exception as e:
            had_error = True
            log_error(source, f"Failed to fetch Gmail email {email_id}: {e}")
            break

    # Update sync token for next run (only when every message was handled)
    if not had_error:
        new_sync_token = client.get_sync_token()
        if new_sync_token:
            set_last_sync_token(account_id, folder, new_sync_token)


def process_o365_account(account):
    """Process Office 365 account via Graph API"""
    account_id = account["id"]
    name = account["name"]
    source = name
    folder = "INBOX"
    delete_after_processing = account.get("delete_after_processing", False)

    # Get valid access token
    access_token = get_valid_token(account_id, "o365")
    if not access_token:
        raise Exception("Failed to get valid Office 365 access token")

    client = O365Client(access_token)

    # Get last delta link for incremental sync
    last_delta_link = get_last_sync_token(account_id, folder)

    # Fetch new email IDs
    email_ids = client.fetch_new_emails(last_delta_link)

    # Process each email. Any failure must keep the delta link unchanged so the
    # failed message is retried on the next run instead of being skipped forever.
    had_error = False
    for email_id in email_ids:
        try:
            # Get email in MIME format
            raw_email = client.get_message_mime(email_id)
            if not raw_email:
                had_error = True
                log_error(source, f"Empty MIME response for Office 365 email {email_id}")
                break

            # Collision-safe stable uid for this provider message
            uid = resolve_provider_uid(source, folder, email_id)
            store_email(source, folder, uid, raw_email)

            # Delete from Office 365 if configured
            if delete_after_processing:
                if not client.delete_message(email_id):
                    log_error(source, f"Failed to delete Office 365 email {email_id}")
        except Exception as e:
            had_error = True
            log_error(source, f"Failed to fetch O365 email {email_id}: {e}")
            break

    # Update delta link for next run (only when every message was handled)
    if not had_error:
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

def get_valid_token(account_id: int, account_type: str) -> str:
    """Get valid OAuth access token, refreshing if necessary"""
    import requests
    
    # Get token from database
    row = query(
        """
        SELECT oauth_access_token, oauth_refresh_token, oauth_token_expiry,
               oauth_client_id, oauth_client_secret
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": account_id}
    ).mappings().first()
    
    if not row or not row["oauth_access_token"]:
        return None
    
    # Decrypt tokens
    try:
        access_token = decrypt_password(row["oauth_access_token"])
        refresh_token = decrypt_password(row["oauth_refresh_token"]) if row["oauth_refresh_token"] else None
    except Exception:
        return None
    
    # Check if token is expired
    now = datetime.now(timezone.utc)
    expiry = row["oauth_token_expiry"]
    
    # If token is still valid (with 5 minute buffer), return it
    if expiry and expiry > now + timedelta(minutes=5):
        return access_token
    
    # Token expired or about to expire, refresh it
    if not refresh_token:
        return None
    
    try:
        if account_type == "gmail":
            token_url = "https://oauth2.googleapis.com/token"
        elif account_type == "o365":
            token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
        else:
            return None

        # Retry network requests transient failures (e.g. during server reboot / network flaps)
        max_attempts = 5
        backoff = 1
        response = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = requests.post(
                    token_url,
                    data={
                        "client_id": row["oauth_client_id"],
                        "client_secret": row["oauth_client_secret"],
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token"
                    },
                    timeout=30,
                )
                response.raise_for_status()
                break
            except (requests.exceptions.RequestException, OSError) as e:
                log_error("OAuth", f"Network error refreshing token for account {account_id} (attempt {attempt}/{max_attempts}): {e}", level="warning")
                if attempt == max_attempts:
                    raise
                time.sleep(backoff)
                backoff *= 2
        token_data = response.json()
        
        new_access_token = token_data.get("access_token")
        new_refresh_token = token_data.get("refresh_token", refresh_token)
        expires_in = token_data.get("expires_in", 3600)
        new_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        # Encrypt and store new tokens
        from security import encrypt_password
        encrypted_access = encrypt_password(new_access_token)
        encrypted_refresh = encrypt_password(new_refresh_token)
        
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :access_token,
                oauth_refresh_token = :refresh_token,
                oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {
                "access_token": encrypted_access,
                "refresh_token": encrypted_refresh,
                "expiry": new_expiry,
                "id": account_id
            }
        )
        
        return new_access_token
        
    except Exception as e:
        log_error("OAuth", f"Failed to refresh token for account {account_id}: {e}")
        return None

def get_settings():
    rows = query("SELECT key, value FROM settings").mappings().all()
    return {r["key"]: r["value"] for r in rows}

def _delete_from_mail_servers(records, accounts_by_name, source_key="source", folder_key="folder", uid_key="uid"):
    """Best-effort deletion of archived messages from their mail servers.

    Only IMAP accounts are supported today; Gmail/O365 deletion during retention
    would require per-account OAuth token management and is skipped (the archived
    copy is removed from the database regardless). Failures are logged per source.
    """
    records_by_source = defaultdict(list)
    for rec in records:
        records_by_source[rec[source_key]].append(rec)

    for source, recs in records_by_source.items():
        account = accounts_by_name.get(source)
        if not account:
            continue

        account_type = account.get("account_type", "imap")
        try:
            if account_type != "imap":
                # Gmail/O365: skipped (would need OAuth token refresh)
                continue

            password = decrypt_password(account["password_encrypted"])
            with ImapConnection(
                host=account["host"],
                port=account["port"],
                username=account["username"],
                password=password,
                use_ssl=account["use_ssl"],
                require_starttls=account["require_starttls"],
            ) as conn:
                # Group by folder
                recs_by_folder = defaultdict(list)
                for rec in recs:
                    recs_by_folder[rec[folder_key]].append(rec[uid_key])

                for folder, uids in recs_by_folder.items():
                    try:
                        conn.select(_quote_imap_folder(folder), readonly=False)
                        for uid in uids:
                            if uid:  # Only try to delete if UID exists
                                try:
                                    conn.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                                except Exception:
                                    pass
                        # Expunge to permanently remove
                        conn.expunge()
                    except Exception as e:
                        log_error("Retention", f"Failed to delete from IMAP folder {folder}: {e}")
        except Exception as e:
            log_error("Retention", f"Failed to delete from mail server {source}: {e}")


def purge_old_emails():
    settings = get_settings()
    enable_purge = settings.get("enable_purge", "false").lower() == "true"
    if not enable_purge:
        return

    # Malformed retention settings must not crash the whole worker loop.
    try:
        retention_value = int(settings.get("retention_value", 1))
    except (TypeError, ValueError):
        log_error("Retention", f"Invalid retention_value setting: {settings.get('retention_value')!r}")
        return
    retention_unit = settings.get("retention_unit", "years")
    delete_from_mail_server = settings.get("retention_delete_from_mail_server", "false").lower() == "true"

    now = datetime.now(timezone.utc)
    if retention_unit == "days":
        cutoff = now - timedelta(days=retention_value)
    elif retention_unit == "months":
        cutoff = now - timedelta(days=retention_value * 30)  # Approximate
    elif retention_unit == "years":
        cutoff = now - timedelta(days=retention_value * 365)  # Approximate
    else:
        log_error("Retention", f"Invalid retention_unit setting: {retention_unit!r}")
        return

    # Account lookup for mail-server deletion (also used by the quarantine purge)
    accounts_by_name = {}
    if delete_from_mail_server:
        accounts = query(
            """
            SELECT id, name, account_type, host, port, username, password_encrypted,
                   use_ssl, require_starttls
            FROM fetch_accounts
            """
        ).mappings().all()
        accounts_by_name = {acc["name"]: acc for acc in accounts}

    # Delete expired emails in batches so a very large archive is drained over a
    # few cycles instead of loading every expired row into memory at once.
    total_deleted = 0
    while True:
        batch = query(
            """
            SELECT id, source, folder, uid
            FROM emails
            WHERE created_at < :cutoff
            ORDER BY id
            LIMIT :batch
            """,
            {"cutoff": cutoff, "batch": PURGE_BATCH_SIZE},
        ).mappings().all()
        if not batch:
            break

        # Delete from mail servers if enabled
        if delete_from_mail_server:
            _delete_from_mail_servers(batch, accounts_by_name)

        execute("DELETE FROM emails WHERE id = ANY(:ids)", {"ids": [r["id"] for r in batch]})
        total_deleted += len(batch)

    # Prune provider-uid mappings for messages that no longer exist, keeping the
    # alias table bounded by the live archive.
    if total_deleted > 0:
        try:
            execute(
                """
                DELETE FROM email_uid_aliases a
                WHERE NOT EXISTS (
                    SELECT 1 FROM emails e
                    WHERE e.source = a.source AND e.folder = a.folder AND e.uid = a.uid
                )
                AND NOT EXISTS (
                    SELECT 1 FROM quarantined_emails q
                    WHERE q.original_source = a.source
                      AND q.original_folder = a.folder
                      AND q.original_uid = a.uid
                )
                """
            )
        except Exception as e:
            log_error("Retention", f"Failed to prune uid aliases: {e}")

    # Track deletion statistics
    if total_deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'retention', :count, :deleted_from_server)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": total_deleted, "deleted_from_server": delete_from_mail_server},
        )
        log_error("Retention", f"Purged {total_deleted} old emails (delete_from_server={delete_from_mail_server})", level="info")

    # Purge expired quarantined emails based on quarantine retention setting
    # (independent cutoff: COALESCE(expires_at, quarantined_at)).
    try:
        try:
            retention_days = int(settings.get('clamav_quarantine_retention_days', '90'))
        except (TypeError, ValueError):
            retention_days = 90
        quarantine_cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        quarantined_to_delete = query(
            """
            SELECT id, original_source, original_folder, original_uid
            FROM quarantined_emails
            WHERE COALESCE(expires_at, quarantined_at) < :cutoff
            """,
            {"cutoff": quarantine_cutoff},
        ).mappings().all()

        if quarantined_to_delete:
            # Delete from mail servers if retention IMAP deletion is enabled
            if delete_from_mail_server:
                _delete_from_mail_servers(
                    quarantined_to_delete,
                    accounts_by_name,
                    source_key="original_source",
                    folder_key="original_folder",
                    uid_key="original_uid",
                )

            # Delete quarantined emails from database
            deleted = query("""
                DELETE FROM quarantined_emails
                WHERE COALESCE(expires_at, quarantined_at) < :cutoff RETURNING id
            """, {"cutoff": quarantine_cutoff}).rowcount

            if deleted:
                # Track quarantined email deletions
                execute(
                    """
                    INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
                    VALUES (CURRENT_DATE, 'quarantine', :count, :deleted_from_server)
                    ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
                    DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
                    """,
                    {"count": deleted, "deleted_from_server": delete_from_mail_server},
                )
                log_error('Quarantine', f'Purged {deleted} quarantined emails older than {retention_days} days (delete_from_server={delete_from_mail_server})', level='info')
    except Exception as e:
        log_error('Quarantine', f'Failed to purge quarantined emails: {e}')

def main_loop():
    # Track last processing time for each account
    last_processed = {}

    while True:
        try:
            # Idempotent and cheap (metadata lookup once the table exists). Re-run
            # each cycle so a transient failure at startup (e.g. DB briefly down)
            # cannot permanently break Gmail/O365 accounts until a restart.
            _ensure_worker_schema()
            accounts = get_accounts()
        except Exception as e:
            # A database outage must not kill the worker; keep polling.
            log_error("Worker", f"Failed to load fetch accounts: {e}")
            time.sleep(60)
            continue

        if not accounts:
            time.sleep(60)  # Check again in 1 minute
            continue

        now = time.time()

        for account in accounts:
            account_id = account["id"]
            poll_interval = account["poll_interval_seconds"] or POLL_INTERVAL_FALLBACK

            # Check if this account is due for processing
            last_run = last_processed.get(account_id, 0)
            time_since_last = now - last_run

            if time_since_last >= poll_interval:
                try:
                    process_account(account)
                except Exception as e:
                    # Belt-and-braces: process_account handles its own errors, but
                    # nothing here may take the whole worker down.
                    log_error("Worker", f"Unexpected error processing account {account_id}: {e}")
                last_processed[account_id] = now

        # Purge old emails after processing all accounts (run once per cycle).
        # Batched internally; failures are logged, never fatal.
        try:
            purge_old_emails()
        except Exception as e:
            log_error("Worker", f"Unexpected error during retention purge: {e}")

        # Short sleep before checking again (60 seconds allows for responsive polling)
        time.sleep(60)

if __name__ == "__main__":
    main_loop()