from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from imaplib import IMAP4, IMAP4_SSL
from typing import List
from ..utils.templates import templates
from ..utils.db import query, execute
from ..utils.logger import log
from ..utils.config import get_config
from ..utils.crypto import decrypt_password
from cryptography.fernet import Fernet
from ..utils.alerts import create_alert
from ..utils.email_parser import compute_signature
from ..utils.timezone import format_datetime
from ..utils.permissions import PermissionChecker, require_permission, PERMISSIONS

router = APIRouter()

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


def _delete_quarantined_from_mail_server_and_db(ids: List[int]) -> tuple[int, list[str]]:
    """
    Delete quarantined emails from mail server (IMAP/Gmail/O365) and then from DB.
    Returns (deleted_count, errors).
    """
    errors: list[str] = []
    deleted = 0

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
                   use_ssl, require_starttls
            FROM fetch_accounts
            WHERE name = :name
            """,
            {"name": email_row["original_source"]},
        ).mappings().first()

        if not account:
            errors.append(f"No fetch account found for source '{email_row['original_source']}' (quarantined email {qid})")
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

            # Select the folder and delete by UID
            folder = email_row["original_folder"]
            conn.select(folder)

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

        except Exception as e:
            errors.append(f"Quarantined email {qid}: {str(e)}")
        finally:
            # Ensure connection is closed even if errors occur
            if conn:
                try:
                    conn.logout()
                except:
                    pass

    # Track deletion statistics
    if deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'quarantine', :count, TRUE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": deleted},
        )

    return deleted, errors


@router.get('/quarantine', response_class=HTMLResponse)
def list_quarantine(request: Request, _=require_permission(PERMISSIONS["view_quarantine"]), q: str = None, virus: str = None, page: int = 1):
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

    where_sql = " AND ".join(where_clauses) if where_clauses else ""
    if where_sql:
        where_sql = f"WHERE {where_sql}"

    # Get total count for pagination
    total_row = query(
        f'SELECT COUNT(*) as total FROM quarantined_emails {where_sql}',
        params
    ).mappings().first()
    total = int(total_row['total'] or 0) if total_row else 0

    rows = query(
        f'''
        SELECT id, original_source, original_folder, date, subject, sender, recipients, virus_name,
               quarantined_at, expires_at, raw_email, compressed, signature
        FROM quarantined_emails
        {where_sql}
        ORDER BY quarantined_at DESC
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

        try:
            stored_sig = ir.get('signature')
            raw_blob = ir.get('raw_email')
            data = raw_blob

            # Ensure bytes for DB blobs (memoryview)
            if isinstance(data, memoryview):
                data = data.tobytes()
            elif data is not None and not isinstance(data, (bytes, bytearray)):
                try:
                    data = bytes(data)
                except Exception:
                    pass

            if data is not None:
                decryption_successful = False

                if fernet:
                    try:
                        data = fernet.decrypt(data)
                        decryption_successful = True
                    except Exception:
                        data = raw_blob
                        decryption_successful = False
                else:
                    decryption_successful = True

                # Gzip decompression if needed
                try:
                    if isinstance(data, (bytes, bytearray)) and len(data) >= 2 and data[:2] == b"\x1f\x8b":
                        import gzip as _gzip
                        data = _gzip.decompress(data)
                except Exception:
                    pass

                try:
                    if decryption_successful or not fernet:
                        current_sig = compute_signature(data)
                    else:
                        current_sig = None
                except Exception:
                    current_sig = None

                integrity_reason = None
                if stored_sig is None:
                    integrity = 'no_signature'
                    integrity_reason = 'No signature was stored when this email was quarantined'
                elif not decryption_successful and fernet:
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
            else:
                integrity = 'no_raw'
                integrity_reason = 'No raw email data available'

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
        if ir.get("date"):
            if hasattr(ir["date"], 'strftime'):
                ir["date_formatted"] = format_datetime(ir["date"], user_id)
            else:
                # Try to parse the date string
                from email.utils import parsedate_to_datetime
                try:
                    parsed_date = parsedate_to_datetime(ir["date"])
                    ir["date_formatted"] = format_datetime(parsed_date, user_id)
                except (ValueError, TypeError):
                    ir["date_formatted"] = ir["date"]
        else:
            ir["date_formatted"] = ir.get("date")

        processed.append(ir)

    msg = request.session.pop('flash', None)

    return templates.TemplateResponse(
        'quarantine.html',
        {
            'request': request,
            'items': processed,
            'q': q or '',
            'virus': virus or '',
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
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
            data = raw
            decryption_successful = False

            if f:
                try:
                    data = f.decrypt(data)
                    decryption_successful = True
                except Exception:
                    data = raw
                    decryption_successful = False
            else:
                decryption_successful = True

            # Gzip decompress if needed
            try:
                if isinstance(data, (bytes, bytearray)) and len(data) >= 2 and data[:2] == b"\x1f\x8b":
                    import gzip as _gzip
                    data = _gzip.decompress(data)
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
            if decryption_successful or not f:
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

    return templates.TemplateResponse(
        'quarantine-view.html',
        {'request': request, 'item': item, 'preview': preview, 'headers': headers, 'body': body}
    )

@router.get('/quarantine/_session')
def quarantine_session(request: Request):
    """Debugging endpoint: returns current session keys for troubleshooting auth issues."""
    try:
        # Only return a limited set of session keys to avoid leaking secrets
        keys = {k: request.session.get(k) for k in ('user_id', 'username', 'permissions')}
        return JSONResponse({'session': keys})
    except Exception as e:
        log('error', 'Quarantine', f'Failed to read session for debug: {e}')
        return JSONResponse({'error': 'failed to read session'})
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
        return RedirectResponse('/quarantine', status_code=303)

    raw = item.get('raw_email')
    f = _get_quarantine_fernet()

    if raw:
        try:
            data = raw
            if f:
                data = f.decrypt(data)

            # Decompress if needed to get the original uncompressed email
            # Use the compressed flag from the database instead of magic bytes detection
            if item.get('compressed'):
                import gzip as _gzip
                data = _gzip.decompress(data)

            # IMPORTANT: Preserve the ORIGINAL signature from quarantine
            # Do NOT compute a new signature, as this would make modified emails appear valid
            sig_to_store = item.get('signature')

            # Re-compress the data for storage (emails table stores compressed data)
            import gzip as _gzip
            compressed_data = _gzip.compress(data)

            vname = item.get('virus_name')
            vdetected = True if vname else False
            vscanned = True

            # Update existing email
            execute(
                """
                UPDATE emails SET raw_email = :raw, signature = :signature, compressed = TRUE,
                    quarantined = FALSE, quarantine_id = NULL,
                    virus_scanned = :vscanned, virus_detected = :vdetected, virus_name = :vname,
                    scan_timestamp = :qtime
                WHERE source = :source AND folder = :folder AND uid = :uid
                """,
                {
                    'raw': compressed_data,
                    'signature': sig_to_store,
                    'vname': vname,
                    'vdetected': vdetected,
                    'vscanned': vscanned,
                    'qtime': item.get('quarantined_at'),
                    'source': item.get('original_source'),
                    'folder': item.get('original_folder'),
                    'uid': item.get('original_uid')
                }
            )

            # Insert if not exists
            execute(
                """
                INSERT INTO emails (source, folder, uid, subject, sender, recipients, date,
                    raw_email, signature, compressed, virus_scanned, virus_detected, virus_name,
                    scan_timestamp, quarantined)
                VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date,
                    :raw_email, :signature, TRUE, :vscanned, :vdetected, :vname,
                    :qtime, FALSE)
                ON CONFLICT (source, folder, uid) DO NOTHING
                """,
                {
                    'source': item.get('original_source'),
                    'folder': item.get('original_folder'),
                    'uid': item.get('original_uid'),
                    'subject': item.get('subject'),
                    'sender': item.get('sender'),
                    'recipients': item.get('recipients'),
                    'date': item.get('date'),
                    'raw_email': compressed_data,
                    'signature': sig_to_store,
                    'vname': vname,
                    'vdetected': vdetected,
                    'vscanned': vscanned,
                    'qtime': item.get('quarantined_at')
                }
            )

        except Exception as e:
            log('error', 'Quarantine', f"Failed to restore quarantined email {qid}: {str(e)}")
            return RedirectResponse(f'/quarantine/{qid}', status_code=303)

    # Delete quarantine record
    execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
    execute(
        'UPDATE emails SET quarantined = FALSE, quarantine_id = NULL '
        'WHERE source = :source AND folder = :folder AND uid = :uid',
        {
            'source': item.get('original_source'),
            'folder': item.get('original_folder'),
            'uid': item.get('original_uid')
        }
    )

    username = request.session.get("username", "unknown")
    log('info', 'Quarantine', f"User '{username}' restored quarantined email {qid}")

    # Create alert
    try:
        create_alert(
            'warning',
            'Quarantined Email Restored',
            f'User {username} restored a quarantined email',
            f'Quarantine ID: {qid}, Original email from {item.get("original_source")}',
            'quarantine_restored'
        )
    except Exception as e:
        log('error', 'Quarantine', f"Failed to create restore alert: {str(e)}")

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
            request.session['flash'] = (
                f"Deleted quarantined email from database and mail server. "
                f"Some errors occurred: {error_text}"
            )
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
    username = request.session.get("username", "unknown")

    for qid in ids:
        try:
            item = query('SELECT * FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
            if not item:
                continue

            raw = item.get('raw_email')
            f = _get_quarantine_fernet()

            if raw:
                try:
                    data = raw
                    if f:
                        data = f.decrypt(data)

                    try:
                        sig = compute_signature(data)
                    except Exception:
                        sig = None

                    # Update existing email
                    execute(
                        """
                        UPDATE emails SET raw_email = :raw, signature = :signature, compressed = TRUE,
                            quarantined = FALSE, quarantine_id = NULL,
                            virus_scanned = TRUE, virus_detected = TRUE, virus_name = :vname,
                            scan_timestamp = :qtime
                        WHERE source = :source AND folder = :folder AND uid = :uid
                        """,
                        {
                            'raw': data,
                            'signature': sig,
                            'vname': item.get('virus_name'),
                            'qtime': item.get('quarantined_at'),
                            'source': item.get('original_source'),
                            'folder': item.get('original_folder'),
                            'uid': item.get('original_uid')
                        }
                    )

                    # Insert if not exists
                    execute(
                        """
                        INSERT INTO emails (source, folder, uid, subject, sender, recipients, date,
                            raw_email, signature, compressed, virus_scanned, virus_detected,
                            virus_name, scan_timestamp, quarantined)
                        VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date,
                            :raw_email, :signature, TRUE, TRUE, TRUE,
                            :vname, :qtime, FALSE)
                        ON CONFLICT (source, folder, uid) DO NOTHING
                        """,
                        {
                            'source': item.get('original_source'),
                            'folder': item.get('original_folder'),
                            'uid': item.get('original_uid'),
                            'subject': item.get('subject'),
                            'sender': item.get('sender'),
                            'recipients': item.get('recipients'),
                            'date': item.get('date'),
                            'raw_email': data,
                            'signature': sig,
                            'vname': item.get('virus_name'),
                            'qtime': item.get('quarantined_at')
                        }
                    )
                except Exception:
                    continue

            # Delete quarantine record
            execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
            execute(
                'UPDATE emails SET quarantined = FALSE, quarantine_id = NULL '
                'WHERE source = :source AND folder = :folder AND uid = :uid',
                {
                    'source': item.get('original_source'),
                    'folder': item.get('original_folder'),
                    'uid': item.get('original_uid')
                }
            )

            restored += 1

        except Exception as e:
            log('error', 'Quarantine', f'Failed to restore quarantined email {qid}: {e}')
            continue

    if restored > 0:
        log('info', 'Quarantine',
            f"User '{username}' restored {restored} quarantined email(s) (IDs: {ids})")

        try:
            create_alert(
                'warning',
                'Quarantined Emails Restored',
                f'User {username} restored {restored} quarantined email(s)',
                f'Quarantine IDs: {ids}',
                'quarantine_restored'
            )
        except Exception as e:
            log('error', 'Quarantine', f"Failed to create restore alert: {str(e)}")

        request.session['flash'] = f"Restored {restored} quarantined email(s)."
    else:
        request.session['flash'] = "No emails were restored."

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
                f"Deleted {deleted} quarantined email(s) from database and mail server. "
                f"Some errors occurred: {error_text}"
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
