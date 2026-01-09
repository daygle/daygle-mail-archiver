from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from utils.templates import templates
from utils.db import query, execute
from utils.logger import log
from utils.config import get_config
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

@router.get('/quarantine', response_class=HTMLResponse)
def list_quarantine(request: Request):
    # Require login first
    if not request.session.get('user_id'):
        return RedirectResponse('/login', status_code=303)

    # Verify role from DB (more reliable than trusting session role)
    try:
        user = query('SELECT role FROM users WHERE id = :id', {'id': request.session.get('user_id')}).mappings().first()
        if not user or user.get('role') != 'administrator':
            log('warning', 'Quarantine', f"Unauthorized access attempt to /quarantine by user_id={request.session.get('user_id')} role={request.session.get('role')}")
            return RedirectResponse('/dashboard', status_code=303)
    except Exception as e:
        log('error', 'Quarantine', f"Failed to verify admin role: {e}")
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
def delete_quarantine(request: Request, qid: int):
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

    execute('DELETE FROM quarantined_emails WHERE id = :id', {'id': qid})
    return RedirectResponse('/quarantine', status_code=303)
