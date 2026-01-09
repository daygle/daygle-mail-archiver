from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from imaplib import IMAP4, IMAP4_SSL
from typing import List
from utils.templates import templates
from utils.db import query, execute
from utils.logger import log
from utils.config import get_config
from utils.security import decrypt_password
from cryptography.fernet import Fernet

router = APIRouter()

def _get_quarantine_fernet():
    key = get_config('CLAMAV_QUARANTINE_KEY')
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        return None


def _delete_quarantined_from_imap_and_db(ids: List[int]) -> tuple[int, list[str]]:
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
def list_quarantine(request: Request):
    # Debug: log session state to diagnose unexpected redirects
    try:
        log('info', 'Quarantine', f"Session keys: {list(request.session.keys())}, user_id={request.session.get('user_id')}, role={request.session.get('role')}, username={request.session.get('username')}")
    except Exception:
        pass

    # Require login first
    if not request.session.get('user_id'):
        request.session['flash'] = 'Please login to access Quarantine'
        return RedirectResponse('/login', status_code=303)

    # Verify role from DB (more reliable than trusting session role)
    try:
        user = query('SELECT role FROM users WHERE id = :id', {'id': request.session.get('user_id')}).mappings().first()
        if not user or user.get('role') != 'administrator':
            log('warning', 'Quarantine', f"Unauthorized access attempt to /quarantine by user_id={request.session.get('user_id')} role={request.session.get('role')}")
            request.session['flash'] = 'Administrator access required'
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify admin role: {e}")
        request.session['flash'] = 'Administrator access required'
        return RedirectResponse('/dashboard', status_code=303)

    rows = query('SELECT id, subject, sender, recipients, virus_name, quarantined_at, expires_at FROM quarantined_emails ORDER BY quarantined_at DESC').mappings().all()
    return templates.TemplateResponse('quarantine.html', {'request': request, 'items': rows})

@router.get('/quarantine/{qid}', response_class=HTMLResponse)
def view_quarantine(request: Request, qid: int):
    # Require login first
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # Verify role from DB (more reliable than trusting session role)
    try:
        user = query('SELECT role FROM users WHERE id = :id', {'id': request.session.get('user_id')}).mappings().first()
        if not user or user.get('role') != 'administrator':
            log('warning', 'Quarantine', f"Unauthorized view attempt to /quarantine/{qid} by user_id={request.session.get('user_id')} role={request.session.get('role')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify admin role: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    item = query('SELECT * FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
    if not item:
        return RedirectResponse('/quarantine', status_code=303)

    raw = item.get('raw_email')
    f = _get_quarantine_fernet()
    preview = None
    if raw:
        try:
            data = raw
            if f:
                data = f.decrypt(data)
            # If data appears to be gzipped, decompress for preview
            try:
                if isinstance(data, (bytes, bytearray)) and len(data) >= 2 and data[:2] == b"\x1f\x8b":
                    import gzip as _gzip
                    data = _gzip.decompress(data)
            except Exception:
                # If decompression fails, fall back to raw bytes
                pass
            preview = data[:10000].decode(errors='replace') if isinstance(data, (bytes, bytearray)) else str(data)
        except Exception:
            preview = '[Could not decrypt or render content]'

    return templates.TemplateResponse('quarantine-view.html', {'request': request, 'item': item, 'preview': preview})


@router.get('/quarantine/_session')
def quarantine_session(request: Request):
    """Debugging endpoint: returns current session keys for troubleshooting auth issues."""
    try:
        # Only return a limited set of session keys to avoid leaking secrets
        keys = {k: request.session.get(k) for k in ('user_id', 'username', 'role')}
        return JSONResponse({'session': keys})
    except Exception as e:
        log('error', 'Quarantine', f'Failed to read session for debug: {e}')
        return JSONResponse({'error': 'failed to read session'})
@router.post('/quarantine/{qid}/restore')
def restore_quarantine(request: Request, qid: int):
    # Require login first
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # Verify role from DB (more reliable than trusting session role)
    try:
        user = query('SELECT role FROM users WHERE id = :id', {'id': request.session.get('user_id')}).mappings().first()
        if not user or user.get('role') != 'administrator':
            log('warning', 'Quarantine', f"Unauthorized restore attempt to /quarantine/{qid}/restore by user_id={request.session.get('user_id')} role={request.session.get('role')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify admin role: {e}")
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
                # Insert or update email in emails table
                execute(
                    """
                    UPDATE emails SET raw_email = :raw, compressed = TRUE, quarantined = FALSE, quarantine_id = NULL, virus_scanned = TRUE, virus_detected = TRUE, virus_name = :vname, scan_timestamp = :qtime
                    WHERE source = :source AND folder = :folder AND uid = :uid
                    """,
                    {
                        'raw': data,
                        'vname': item.get('virus_name'),
                        'qtime': item.get('quarantined_at'),
                        'source': item.get('original_source'),
                        'folder': item.get('original_folder'),
                        'uid': item.get('original_uid')
                    }
                )
                # If no row updated, insert a new row
                execute(
                    """
                    INSERT INTO emails (source, folder, uid, subject, sender, recipients, date, raw_email, compressed, virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined)
                    VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :raw_email, TRUE, TRUE, TRUE, :vname, :qtime, TRUE)
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
                        'vname': item.get('virus_name'),
                        'qtime': item.get('quarantined_at')
                    }
                )
            except Exception:
                # If restore fails, do not delete quarantine
                return RedirectResponse(f'/quarantine/{qid}', status_code=303)

        # Delete quarantine record
        execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
        # Clear quarantine flag on emails table
        execute('UPDATE emails SET quarantined = FALSE, quarantine_id = NULL WHERE source = :source AND folder = :folder AND uid = :uid', {'source': item.get('original_source'), 'folder': item.get('original_folder'), 'uid': item.get('original_uid')})

    return RedirectResponse('/quarantine', status_code=303)

@router.post('/quarantine/{qid}/delete')
def delete_quarantine(request: Request, qid: int, mode: str = Form("db")):
    """
    Delete quarantined email, optionally also from mail server.
    """
    # Require login first
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # Verify role from DB (more reliable than trusting session role)
    try:
        user = query('SELECT role FROM users WHERE id = :id', {'id': request.session.get('user_id')}).mappings().first()
        if not user or user.get('role') != 'administrator':
            log('warning', 'Quarantine', f"Unauthorized delete attempt to /quarantine/{qid}/delete by user_id={request.session.get('user_id')} role={request.session.get('role')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify admin role: {e}")
        return RedirectResponse('/dashboard', status_code=303)

    # Check if quarantined email exists
    item = query('SELECT id FROM quarantined_emails WHERE id = :id', {'id': qid}).mappings().first()
    if not item:
        request.session['flash'] = f"Quarantined email #{qid} not found."
        return RedirectResponse('/quarantine', status_code=303)

    if mode == "db":
        execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
        username = request.session.get("username", "unknown")
        log("warning", "Quarantine", f"User '{username}' deleted quarantined email {qid} from database", "")
        request.session['flash'] = "Quarantined email deleted from database."
        return RedirectResponse('/quarantine', status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_quarantined_from_imap_and_db([qid])

        username = request.session.get("username", "unknown")
        if errors:
            error_text = " | ".join(errors)
            log("warning", "Quarantine", f"User '{username}' deleted quarantined email {qid} from IMAP and database with errors", error_text)
            request.session['flash'] = f"Deleted quarantined email from database and mail server. Some errors occurred: {error_text}"
        else:
            log("warning", "Quarantine", f"User '{username}' deleted quarantined email {qid} from IMAP and database", "")
            request.session['flash'] = "Deleted quarantined email from database and mail server."

        return RedirectResponse('/quarantine', status_code=303)

    else:
        request.session['flash'] = "Invalid delete mode selected."
        return RedirectResponse('/quarantine', status_code=303)
