import gzip
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from imaplib import IMAP4, IMAP4_SSL
from typing import List
from sqlalchemy import text
from ..utils.templates import templates
from ..utils.db import query, execute, transaction
from ..utils.logger import log
from ..utils.config import get_config
from ..utils.crypto import decrypt_password
from cryptography.fernet import Fernet
from ..utils.alerts import create_alert
from ..utils.email_parser import compute_signature, decompress
from ..utils.timezone import format_datetime, format_email_date, user_date_to_utc_range_start, user_date_to_utc_range_end
from ..utils.permissions import PermissionChecker, require_permission, PERMISSIONS
from .emails import _quote_imap_folder

router = APIRouter()

# Mapping of user-facing sort keys to actual DB column names (allowlist to prevent injection)
VALID_QUARANTINE_SORT_COLUMNS = {
    "date": "date",
    "original_source": "original_source",
    "sender": "sender",
    "subject": "subject",
    "virus_name": "virus_name",
    "quarantined_at": "quarantined_at",
}

def require_login(request: Request):
    return "user_id" in request.session

def _get_quarantine_fernet():
    key = get_config('CLAMAV_QUARANTINE_KEY')
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def _looks_like_fernet_token(data) -> bool:
    """Return True if the bytes look like an encrypted Fernet token.

    Every Fernet token starts with the same 6-character prefix ('gAAAAA'), so
    this reliably distinguishes encrypted quarantine blobs from plaintext ones
    (e.g. items quarantined before quarantine encryption was enabled).
    """
    # psycopg2 returns BYTEA columns as memoryview; normalize first so
    # encrypted blobs are recognised regardless of the container type.
    if isinstance(data, memoryview):
        data = data.tobytes()
    if not isinstance(data, (bytes, bytearray)):
        return False
    try:
        return bytes(data[:6]).decode("ascii") == "gAAAAA"
    except Exception:
        return False


def _decrypt_quarantine_raw(raw, fernet) -> tuple:
    """Decrypt quarantine raw bytes when they are actually encrypted.

    Returns ``(data, status)`` where status is one of:
      - ``'plain'``     data was never encrypted (no key configured, or the blob
                        is not a Fernet token)
      - ``'decrypted'`` data was successfully decrypted
      - ``'failed'``    blob looks encrypted but could not be decrypted
                        (missing/rotated CLAMAV_QUARANTINE_KEY); ``data`` is the
                        original encrypted blob and must NOT be treated as raw
    """
    if raw is None:
        return None, 'plain'
    # Normalize memoryview blobs (psycopg2 BYTEA) so the token check and
    # Fernet.decrypt see plain bytes.
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    elif not isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw)
        except Exception:
            return raw, 'plain'
    if fernet is None:
        return raw, 'plain'
    if not _looks_like_fernet_token(raw):
        return raw, 'plain'
    try:
        return fernet.decrypt(raw), 'decrypted'
    except Exception:
        return raw, 'failed'


def _prepare_restore_item(item, fernet) -> tuple:
    """Prepare a quarantined item for restore without touching the database.

    Returns ``(payload, error)``: a dict of values ready to write to the emails
    table, or ``(None, error_message)`` when the item cannot be restored.

    The ORIGINAL signature is preserved (never recomputed), so unmodified emails
    keep passing the integrity check after restore while tampered ones still
    report as modified. The raw email is decompressed and re-compressed, and
    scan metadata / date_parsed are carried through unchanged.
    """
    qid = item.get("id")
    raw = item.get("raw_email")
    if not raw:
        return None, f"Quarantined email {qid} has no raw data to restore"
    if item.get("original_uid") is None:
        # emails.uid is NOT NULL; a legacy item without a recorded UID cannot
        # be re-inserted under its original identity.
        return None, f"Quarantined email {qid} has no original UID recorded and cannot be restored"

    try:
        data, decrypt_status = _decrypt_quarantine_raw(raw, fernet)
        if decrypt_status == "failed":
            return None, (
                f"Quarantined email {qid} is encrypted and could not be decrypted "
                f"(is CLAMAV_QUARANTINE_KEY configured correctly?)"
            )

        # Ensure bytes for DB blobs (memoryview)
        if isinstance(data, memoryview):
            data = data.tobytes()
        elif data is not None and not isinstance(data, (bytes, bytearray)):
            try:
                data = bytes(data)
            except Exception:
                return None, f"Quarantined email {qid} raw data is not bytes"

        # Decompress to the original uncompressed email (emails table stores compressed)
        if item.get("compressed"):
            try:
                data = decompress(data, True)
            except Exception:
                return None, f"Quarantined email {qid} could not be decompressed (corrupt data?)"
        elif isinstance(data, (bytes, bytearray)) and data[:2] == b"\x1f\x8b":
            # Legacy row with a wrong compressed flag: the blob is actually gzip.
            # Uncompress it so the restored email keeps a valid integrity check.
            try:
                data = decompress(data, True)
            except Exception:
                pass  # leave as-is; the signature comparison reports the mismatch

        # IMPORTANT: preserve the ORIGINAL signature from quarantine. Do NOT
        # compute a new one, as that would make modified emails appear valid.
        sig_to_store = item.get("signature")

        # emails table always stores gzip-compressed raw bytes
        try:
            compressed_data = gzip.compress(data)
        except Exception:
            return None, f"Quarantined email {qid} could not be compressed for storage"

        vname = item.get("virus_name")
        vdetected = item.get("virus_detected")
        if vdetected is None:
            vdetected = bool(vname)
        vscanned = item.get("virus_scanned")
        if vscanned is None:
            # Legacy rows pre-date the scan-metadata columns. In that era every
            # archived email was recorded as scanned (the old code set
            # virus_scanned=TRUE unconditionally after calling scan()), so True
            # is the historically accurate default rather than a dishonest one.
            vscanned = True

        return {
            "source": item.get("original_source"),
            "folder": item.get("original_folder"),
            "uid": item.get("original_uid"),
            "subject": item.get("subject"),
            "sender": item.get("sender"),
            "recipients": item.get("recipients"),
            "date": item.get("date"),
            "date_parsed": item.get("date_parsed"),
            "message_id": item.get("message_id"),
            "raw": compressed_data,
            "signature": sig_to_store,
            "vscanned": vscanned,
            "vdetected": vdetected,
            "vname": vname,
            "scan_ts": item.get("scan_timestamp"),
            "original_created_at": item.get("original_created_at"),
        }, None
    except Exception as e:
        return None, f"Failed to prepare quarantined email {qid} for restore: {str(e)}"


def _restore_quarantine_item(item) -> tuple:
    """Restore one quarantined email back into the emails table (atomic).

    Returns ``(success, error_message)``. On failure the quarantine record is
    left untouched so nothing is silently lost.
    """
    qid = item.get("id")
    payload, error = _prepare_restore_item(item, _get_quarantine_fernet())
    if payload is None:
        return False, error

    try:
        with transaction() as conn:
            # Restore under the original identity. If an email with the same
            # (source, folder, uid) already exists - e.g. the message was
            # re-fetched and archived after it was quarantined - do NOT silently
            # overwrite it (that could replace a clean re-fetched copy with the
            # infected quarantined one). Surface the conflict and keep the
            # quarantine record so nothing is lost.
            # Use the original created_at if available, otherwise let the DB default to NOW()
            if payload.get("original_created_at"):
                result = conn.execute(
                    text(
                        """
                        INSERT INTO emails (source, folder, uid, subject, sender, recipients, date,
                            date_parsed, message_id, raw_email, signature, compressed,
                            virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined,
                            created_at)
                        VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date,
                            :date_parsed, :message_id, :raw, :signature, TRUE,
                            :vscanned, :vdetected, :vname, :scan_ts, FALSE,
                            :original_created_at)
                        ON CONFLICT (source, folder, uid) DO NOTHING
                        """
                    ),
                    payload,
                )
            else:
                result = conn.execute(
                    text(
                        """
                        INSERT INTO emails (source, folder, uid, subject, sender, recipients, date,
                            date_parsed, message_id, raw_email, signature, compressed,
                            virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined)
                        VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date,
                            :date_parsed, :message_id, :raw, :signature, TRUE,
                            :vscanned, :vdetected, :vname, :scan_ts, FALSE)
                        ON CONFLICT (source, folder, uid) DO NOTHING
                        """
                    ),
                    payload,
                )
            if result.rowcount == 0:
                raise RuntimeError(
                    f"An email with the same account/folder/UID already exists in the archive; "
                    f"restore of quarantined email {qid} was skipped to avoid overwriting it"
                )

            # Remove the quarantine record
            conn.execute(text("DELETE FROM quarantined_emails WHERE id = :id"), {"id": qid})

        return True, None
    except Exception as e:
        return False, f"Failed to restore quarantined email {qid}: {str(e)}"


def _delete_quarantined_from_mail_server_and_db(ids: List[int]) -> tuple[int, list[str]]:
    """
    Delete quarantined emails from mail server (IMAP/Gmail/O365) and then from DB.
    Returns (deleted_count, errors).
    """
    errors: list[str] = []
    deleted = 0
    server_deleted = 0
    db_only_deleted = 0

    for qid in ids:
        email_row = query(
            """
            SELECT id, original_source, original_folder, original_uid
            FROM quarantined_emails
            WHERE id = :id
            """,
            {"id": qid},
        ).mappings().first()

        if not email_row:
            errors.append(f"Quarantined email {qid} not found")
            continue

        account = query(
            """
            SELECT name, host, port, username, password_encrypted,
                   use_ssl, require_starttls, account_type
            FROM fetch_accounts
            WHERE name = :name
            """,
            {"name": email_row["original_source"]},
        ).mappings().first()

        if not account:
            # The fetch account no longer exists, so the mail server copy cannot
            # be addressed; remove the database record rather than leaving the
            # quarantine item stuck.
            query("DELETE FROM quarantined_emails WHERE id = :id", {"id": qid})
            deleted += 1
            db_only_deleted += 1
            errors.append(
                f"Quarantined email {qid}: fetch account '{email_row['original_source']}' "
                f"no longer exists; deleted from database only"
            )
            continue

        account_type = account.get("account_type", "imap")
        original_uid = email_row.get("original_uid")

        # Gmail/O365 accounts are API-based (no IMAP mailbox to delete from),
        # items without a recorded original UID cannot be addressed on the
        # server, and negative UIDs mark imported emails with no server copy.
        # Fall back to removing the database record only rather than failing.
        if account_type != "imap" or original_uid is None or original_uid <= 0:
            query("DELETE FROM quarantined_emails WHERE id = :id", {"id": qid})
            deleted += 1
            db_only_deleted += 1
            if account_type != "imap":
                errors.append(
                    f"Quarantined email {qid}: mail server deletion not supported for "
                    f"{account_type} accounts; deleted from database only"
                )
            elif original_uid is None:
                errors.append(
                    f"Quarantined email {qid}: no original UID recorded; deleted from database only"
                )
            else:
                errors.append(
                    f"Quarantined email {qid}: no mail server copy (imported email); "
                    f"deleted from database only"
                )
            continue

        conn = None
        try:
            # Connect to IMAP using same style as /fetch_accounts/test
            if account["use_ssl"]:
                conn = IMAP4_SSL(account["host"], account["port"])
            else:
                conn = IMAP4(account["host"], account["port"])
                if account["require_starttls"]:
                    conn.starttls()

            password = decrypt_password(account["password_encrypted"])
            conn.login(account["username"], password)

            # Select the folder and delete by UID (quote the folder name so
            # mailboxes containing spaces, e.g. "Sent Items", are accepted)
            folder = email_row["original_folder"]
            conn.select(_quote_imap_folder(folder))

            uid_str = str(email_row["original_uid"])
            typ, _ = conn.uid("STORE", uid_str, "+FLAGS", r"(\Deleted)")
            if typ != "OK":
                raise RuntimeError(f"Failed to flag quarantined email {qid} for deletion on mail server")

            typ, _ = conn.expunge()
            if typ != "OK":
                raise RuntimeError(f"Failed to expunge quarantined email {qid} on mail server")

            # Only delete from DB if IMAP delete succeeded
            query(
                "DELETE FROM quarantined_emails WHERE id = :id",
                {"id": qid},
            )
            deleted += 1
            server_deleted += 1

        except Exception as e:
            errors.append(f"Quarantined email {qid}: {str(e)}")
        finally:
            # Ensure connection is closed even if errors occur
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

    # Track deletion statistics (server deletions and DB-only fallbacks separately)
    if server_deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'quarantine', :count, TRUE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": server_deleted},
        )
    if db_only_deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'quarantine', :count, FALSE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": db_only_deleted},
        )

    return deleted, errors


@router.get('/quarantine', response_class=HTMLResponse)
def list_quarantine(
    request: Request,
    _=require_permission(PERMISSIONS["view_quarantine"]),
    q: str = None,
    virus: str = None,
    account: str = None,
    sender: str = None,
    recipient: str = None,
    subject: str = None,
    date_from: str = None,
    date_to: str = None,
    quarantined_from: str = None,
    quarantined_to: str = None,
    page: int = 1,
    sort_by: str = None,
    sort_order: str = None,
):
    # Require login first
    if not request.session.get('user_id'):
        request.session['flash'] = 'Please login to access Quarantine'
        return RedirectResponse('/login', status_code=303)

    # Get user_id for timezone formatting
    user_id = request.session.get('user_id')
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

    # Pagination: determine page_size from user or global settings
    page_size = 50
    if user_id:
        try:
            u = query("SELECT page_size FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            if u and u.get('page_size'):
                page_size = int(u.get('page_size'))
        except Exception:
            pass

    if not user_id or not page_size:
        try:
            global_result = query("SELECT value FROM settings WHERE key = 'page_size'").mappings().first()
            if global_result and global_result.get('value'):
                page_size = int(global_result.get('value'))
        except Exception:
            pass

    page_size = min(max(10, int(page_size or 50)), 500)
    page = max(1, int(page or 1))
    offset = (page - 1) * page_size

    # Build query with optional filters
    where_clauses = []
    params = {}

    if q:
        where_clauses.append("(subject ILIKE :q OR sender ILIKE :q OR recipients ILIKE :q)")
        params['q'] = f'%{q}%'

    if virus:
        where_clauses.append("virus_name ILIKE :virus")
        params['virus'] = f'%{virus}%'

    if account:
        where_clauses.append("original_source ILIKE :account")
        params['account'] = f'%{account}%'

    if sender:
        where_clauses.append("sender ILIKE :sender")
        params['sender'] = f'%{sender}%'

    if recipient:
        where_clauses.append("recipients ILIKE :recipient")
        params['recipient'] = f'%{recipient}%'

    if subject:
        where_clauses.append("subject ILIKE :subject")
        params['subject'] = f'%{subject}%'

    if date_from:
        where_clauses.append("COALESCE(date_parsed, quarantined_at) >= :date_from_utc")
        params['date_from_utc'] = user_date_to_utc_range_start(date_from, user_id)

    if date_to:
        where_clauses.append("COALESCE(date_parsed, quarantined_at) < :date_to_utc")
        params['date_to_utc'] = user_date_to_utc_range_end(date_to, user_id)

    if quarantined_from:
        where_clauses.append("quarantined_at >= :quarantined_from_utc")
        params['quarantined_from_utc'] = user_date_to_utc_range_start(quarantined_from, user_id)

    if quarantined_to:
        where_clauses.append("quarantined_at < :quarantined_to_utc")
        params['quarantined_to_utc'] = user_date_to_utc_range_end(quarantined_to, user_id)

    where_sql = " AND ".join(where_clauses) if where_clauses else ""
    if where_sql:
        where_sql = f"WHERE {where_sql}"

    # Build ORDER BY from validated allowlist to prevent SQL injection
    sort_col = VALID_QUARANTINE_SORT_COLUMNS.get(sort_by, "quarantined_at")
    sort_dir = "ASC" if sort_order == "asc" else "DESC"
    # For the date column (stored as TEXT), use the pre-parsed TIMESTAMPTZ column for
    # correct chronological ordering. Fall back to quarantined_at when date_parsed is NULL.
    if sort_col == "date":
        order_sql = f"COALESCE(date_parsed, quarantined_at) {sort_dir}, id {sort_dir}"
    else:
        order_sql = f"{sort_col} {sort_dir}, id {sort_dir}"

    # Get total count for pagination
    total_row = query(
        f'SELECT COUNT(*) as total FROM quarantined_emails {where_sql}',
        params
    ).mappings().first()
    total = int(total_row['total'] or 0) if total_row else 0

    rows = query(
        f'''
        SELECT id, original_source, original_folder, date, subject, sender, recipients, virus_name,
               quarantined_at, expires_at, signature,
               -- Only transfer raw bytes for rows that have a signature to check;
               -- signature-less rows short-circuit to 'no_signature' without them.
               CASE WHEN signature IS NOT NULL THEN raw_email ELSE NULL END AS raw_email,
               CASE WHEN signature IS NOT NULL THEN compressed ELSE NULL END AS compressed
        FROM quarantined_emails
        {where_sql}
        ORDER BY {order_sql}
        LIMIT :limit OFFSET :offset
        ''',
        {**params, 'limit': page_size, 'offset': offset}
    ).mappings().all()

    total_pages = (total + page_size - 1) // page_size if page_size else 1

    # Compute integrity for each quarantined item if possible
    processed = []
    fernet = _get_quarantine_fernet()

    for r in rows:
        ir = dict(r)
        integrity = 'unknown'
        integrity_reason = None

        try:
            stored_sig = ir.get('signature')
            if stored_sig is None:
                # Signature-less rows short-circuit: the SQL CASE already left
                # raw_email NULL, so no decrypt/decompress is performed.
                integrity = 'no_signature'
                integrity_reason = 'No signature was stored when this email was quarantined'
            else:
                data = ir.get('raw_email')
                if data is None:
                    integrity = 'no_raw'
                    integrity_reason = 'No raw email data available'
                else:
                    data, decrypt_status = _decrypt_quarantine_raw(data, fernet)
                    decryption_successful = decrypt_status != 'failed'

                    # Ensure bytes for DB blobs (memoryview)
                    if isinstance(data, memoryview):
                        data = data.tobytes()
                    elif data is not None and not isinstance(data, (bytes, bytearray)):
                        try:
                            data = bytes(data)
                        except Exception:
                            pass

                    # Decompress using the stored flag (same as the emails list)
                    if data is not None:
                        try:
                            data = decompress(data, bool(ir.get('compressed')))
                        except Exception:
                            pass  # leave data as-is; the signature comparison will report the mismatch

                    try:
                        current_sig = compute_signature(data) if decryption_successful else None
                    except Exception:
                        current_sig = None

                    if not decryption_successful:
                        integrity = 'encrypted'
                        integrity_reason = 'Could not decrypt file to verify integrity'
                    elif current_sig is None:
                        integrity = 'unknown'
                        integrity_reason = 'Could not compute current signature'
                    elif stored_sig == current_sig:
                        integrity = 'ok'
                        integrity_reason = 'The current hash matches the original signature'
                    else:
                        integrity = 'modified'
                        integrity_reason = 'Stored hash does not match current hash'

        except Exception as e:
            integrity = 'unknown'
            integrity_reason = f'Error checking integrity: {str(e)}'

        ir.pop('raw_email', None)
        ir.pop('compressed', None)
        ir['integrity'] = integrity
        ir['integrity_reason'] = integrity_reason

        # Format quarantined_at
        if ir["quarantined_at"] and hasattr(ir["quarantined_at"], 'strftime'):
            ir["quarantined_at_formatted"] = format_datetime(ir["quarantined_at"], user_id)
        else:
            ir["quarantined_at_formatted"] = ir["quarantined_at"]
        
        # Format date field
        ir["date_formatted"] = format_email_date(ir.get("date"), ir.get("quarantined_at"), user_id)

        processed.append(ir)

    msg = request.session.pop('flash', None)

    return templates.TemplateResponse(
        'quarantine.html',
        {
            'request': request,
            'items': processed,
            'q': q or '',
            'virus': virus or '',
            'account': account or '',
            'sender': sender or '',
            'recipient': recipient or '',
            'subject': subject or '',
            'date_from': date_from or '',
            'date_to': date_to or '',
            'quarantined_from': quarantined_from or '',
            'quarantined_to': quarantined_to or '',
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
            'sort_by': sort_by or '',
            'sort_order': sort_order or '',
            'flash': msg
        }
    )

@router.get('/quarantine/{qid}', response_class=HTMLResponse)
def view_quarantine(request: Request, qid: int):
    # Require login
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # RBAC: require permission to view quarantine
    try:
        checker = PermissionChecker(request)
        if not checker.has_permission("view_quarantine"):
            log('warning', 'Quarantine',
                f"Unauthorized view attempt to /quarantine/{qid} by user_id={request.session.get('user_id')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify permissions: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    # Fetch quarantined item
    item = query('SELECT * FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
    if not item:
        return RedirectResponse('/quarantine', status_code=303)

    # Timezone formatting
    user_id = request.session.get("user_id")
    try:
        user_id = int(user_id)
    except Exception:
        user_id = None

    item = dict(item)
    if item["quarantined_at"] and hasattr(item["quarantined_at"], 'strftime'):
        item["quarantined_at_formatted"] = format_datetime(item["quarantined_at"], user_id)
    else:
        item["quarantined_at_formatted"] = item["quarantined_at"]

    # Process raw email for preview, headers, body, integrity
    raw = item.get('raw_email')
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    elif raw is not None and not isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw)
        except Exception:
            pass

    f = _get_quarantine_fernet()
    preview = None
    integrity = 'unknown'
    current_sig = None
    headers = {}
    body = {}

    if raw:
        try:
            data, decrypt_status = _decrypt_quarantine_raw(raw, f)
            decryption_successful = decrypt_status != 'failed'

            # Ensure bytes for DB blobs (memoryview)
            if isinstance(data, memoryview):
                data = data.tobytes()
            elif data is not None and not isinstance(data, (bytes, bytearray)):
                try:
                    data = bytes(data)
                except Exception:
                    pass

            # Decompress using the stored flag
            if data is not None:
                try:
                    data = decompress(data, bool(item.get('compressed')))
                except Exception:
                    pass

            preview = data[:10000].decode(errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)

            # Parse email
            try:
                from ..utils.email_parser import parse_email
                parsed = parse_email(data)
                headers = parsed.get('headers', {})
                body = parsed.get('body', {})
            except Exception as parse_e:
                log('warning', 'Quarantine', f'Failed to parse email content for quarantine item {qid}: {parse_e}')

            # Integrity check
            integrity_reason = None
            if decryption_successful:
                try:
                    current_sig = compute_signature(data)
                    stored_sig = item.get('signature')
                    if stored_sig is None:
                        integrity = 'no_signature'
                        integrity_reason = 'No signature was stored when this email was quarantined'
                    elif current_sig is None:
                        integrity = 'unknown'
                        integrity_reason = 'Could not compute current signature'
                    elif stored_sig == current_sig:
                        integrity = 'ok'
                        integrity_reason = 'The current hash matches the original signature'
                    else:
                        integrity = 'modified'
                        integrity_reason = 'Stored hash does not match current hash'
                except Exception as e:
                    integrity = 'unknown'
                    integrity_reason = f'Could not read attachment file from storage: {str(e)}'
            else:
                integrity = 'encrypted'
                integrity_reason = 'Could not decrypt file to verify integrity'

        except Exception as e:
            log('error', 'Quarantine', f'Failed to process quarantined email {qid}: {e}')
            preview = '[Could not decrypt or render content]'
            integrity = 'unknown'
            integrity_reason = f'Error processing email: {str(e)}'

    item['integrity'] = integrity
    item['integrity_reason'] = integrity_reason
    item['current_signature'] = current_sig

    msg = request.session.pop('flash', None)

    return templates.TemplateResponse(
        'quarantine-view.html',
        {'request': request, 'item': item, 'preview': preview, 'headers': headers, 'body': body, 'flash': msg}
    )
@router.post('/quarantine/{qid}/restore')
def restore_quarantine(request: Request, qid: int):
    # Require login
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # RBAC: require permission to restore quarantine
    try:
        checker = PermissionChecker(request)
        if not checker.has_permission("restore_quarantine"):
            log('warning', 'Quarantine',
                f"Unauthorized restore attempt to /quarantine/{qid}/restore by user_id={request.session.get('user_id')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify permissions: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    # Fetch quarantined item
    item = query('SELECT * FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
    if not item:
        request.session['flash'] = f"Quarantined email #{qid} not found."
        return RedirectResponse('/quarantine', status_code=303)

    success, error = _restore_quarantine_item(item)
    if not success:
        # Quarantine record is intentionally left in place on failure
        log('error', 'Quarantine', error)
        request.session['flash'] = error
        return RedirectResponse('/quarantine', status_code=303)

    username = request.session.get("username", "unknown")
    log('info', 'Quarantine', f"User '{username}' restored quarantined email {qid}")

    # Create alert
    try:
        create_alert(
            'warning',
            'Quarantined Email Restored',
            f'User {username} restored a quarantined email',
            f'Quarantine ID: {qid}, Original email from {item.get("original_source")}',
            trigger_key='quarantine_restored',
        )
    except Exception as e:
        log('error', 'Quarantine', f"Failed to create restore alert: {str(e)}")

    request.session['flash'] = f"Quarantined email {qid} restored."
    return RedirectResponse('/quarantine', status_code=303)

@router.post('/quarantine/{qid}/delete')
def delete_quarantine(request: Request, qid: int, mode: str = Form("db")):
    """
    Delete quarantined email, optionally also from mail server.
    """
    # Require login
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # RBAC: require permission to delete quarantine
    try:
        checker = PermissionChecker(request)
        if not checker.has_permission("delete_quarantine"):
            log('warning', 'Quarantine',
                f"Unauthorized delete attempt to /quarantine/{qid}/delete "
                f"by user_id={request.session.get('user_id')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify permissions: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    # Check if quarantined email exists
    item = query(
        'SELECT id FROM quarantined_emails WHERE id = :id',
        {'id': qid}
    ).mappings().first()

    if not item:
        request.session['flash'] = f"Quarantined email #{qid} not found."
        return RedirectResponse('/quarantine', status_code=303)

    username = request.session.get("username", "unknown")

    # Delete only from DB
    if mode == "db":
        execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'quarantine', 1, FALSE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """
        )
        log("warning", "Quarantine",
            f"User '{username}' deleted quarantined email {qid} from database")
        request.session['flash'] = "Quarantined email deleted from database."
        return RedirectResponse('/quarantine', status_code=303)

    # Delete from IMAP + DB
    elif mode == "imap":
        deleted, errors = _delete_quarantined_from_mail_server_and_db([qid])

        if errors:
            error_text = " | ".join(errors)
            log("warning", "Quarantine",
                f"User '{username}' deleted quarantined email {qid} from IMAP and DB with errors: {error_text}")
            if deleted > 0:
                request.session['flash'] = (
                    f"Quarantined email deleted from the database. "
                    f"Mail server deletion had issues: {error_text}"
                )
            else:
                request.session['flash'] = f"Quarantined email could not be deleted: {error_text}"
        else:
            log("warning", "Quarantine",
                f"User '{username}' deleted quarantined email {qid} from IMAP and database")
            request.session['flash'] = "Quarantined email deleted from mail server and database."

        return RedirectResponse('/quarantine', status_code=303)

@router.post("/quarantine/restore")
def perform_bulk_restore(request: Request, ids: List[int] = Form(...)):
    # Require login
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # RBAC: require permission to restore quarantine
    try:
        checker = PermissionChecker(request)
        if not checker.has_permission("restore_quarantine"):
            log('warning', 'Quarantine',
                f"Unauthorized bulk restore attempt by user_id={request.session.get('user_id')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify permissions: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    restored = 0
    errors = []
    username = request.session.get("username", "unknown")

    for qid in ids:
        try:
            item = query('SELECT * FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
            if not item:
                errors.append(f"Quarantined email {qid} not found")
                continue

            ok, err = _restore_quarantine_item(item)
            if ok:
                restored += 1
            else:
                errors.append(err)

        except Exception as e:
            errors.append(f"Failed to restore quarantined email {qid}: {e}")

    if restored > 0:
        log('info', 'Quarantine',
            f"User '{username}' restored {restored} quarantined email(s) (IDs: {ids})")

        try:
            create_alert(
                'warning',
                'Quarantined Emails Restored',
                f'User {username} restored {restored} quarantined email(s)',
                f'Quarantine IDs: {ids}',
                trigger_key='quarantine_restored',
            )
        except Exception as e:
            log('error', 'Quarantine', f"Failed to create restore alert: {str(e)}")

        flash_msg = f"Restored {restored} quarantined email(s)."
        if errors:
            flash_msg += " Some errors occurred: " + " | ".join(errors)
        request.session['flash'] = flash_msg
    else:
        flash_msg = "No emails were restored."
        if errors:
            flash_msg += " Errors: " + " | ".join(errors)
        request.session['flash'] = flash_msg

    return RedirectResponse("/quarantine", status_code=303)

@router.post("/quarantine/delete")
def perform_bulk_delete(
    request: Request,
    ids: List[int] = Form(None),
    mode: str = Form("db"),  # "db" or "imap"
):
    """
    Bulk delete selected quarantined emails.
    """
    # Require login
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # RBAC: require permission to delete quarantine
    try:
        checker = PermissionChecker(request)
        if not checker.has_permission("delete_quarantine"):
            log('warning', 'Quarantine',
                f"Unauthorized bulk delete attempt by user_id={request.session.get('user_id')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify permissions: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = 0
        for qid in ids:
            try:
                execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
                deleted += 1
            except Exception:
                continue

        if deleted > 0:
            execute(
                """
                INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
                VALUES (CURRENT_DATE, 'quarantine', :count, FALSE)
                ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
                DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
                """,
                {"count": deleted},
            )

        username = request.session.get("username", "unknown")
        log("warning", "Quarantine",
            f"User '{username}' bulk deleted {deleted} quarantined email(s) from database (IDs: {ids})")
        request.session['flash'] = f"Deleted {deleted} quarantined email(s) from the database."
        return RedirectResponse("/quarantine", status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_quarantined_from_mail_server_and_db(ids)

        username = request.session.get("username", "unknown")
        if errors:
            error_text = " | ".join(errors)
            log("warning", "Quarantine",
                f"User '{username}' bulk deleted {deleted} quarantined email(s) from IMAP and DB with errors (IDs: {ids})",
                error_text)
            request.session['flash'] = (
                f"Deleted {deleted} quarantined email(s) from the database. "
                f"Mail server deletion had issues: {error_text}"
            )
        else:
            log("warning", "Quarantine",
                f"User '{username}' bulk deleted {deleted} quarantined email(s) from IMAP and database (IDs: {ids})")
            request.session['flash'] = (
                f"Deleted {deleted} quarantined email(s) from database and mail server."
            )

        return RedirectResponse("/quarantine", status_code=303)

    else:
        request.session['flash'] = "Invalid delete mode selected."
        return RedirectResponse("/quarantine", status_code=303)
