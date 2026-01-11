from typing import List
import io
import gzip
import zipfile
import mailbox
from email.utils import parsedate_to_datetime
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse
from imaplib import IMAP4, IMAP4_SSL

from utils.db import query, execute
from utils.email_parser import decompress, parse_email
from utils.email_parser import compute_signature
from utils.security import decrypt_password, can_delete
from utils.logger import log
from utils.templates import templates
from utils.timezone import format_datetime
from utils.alerts import create_alert
from utils.permissions import PermissionChecker
from utils.clamav_scanner import ClamAVScanner

router = APIRouter()


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str, category: str = 'info'):
    request.session["flash"] = {"message": message, "type": category}


@router.get("/emails", response_class=HTMLResponse)
def list_emails(
    request: Request,
    page: int = 1,
    q: str | None = None,
    account: str | None = None,
    folder: str | None = None,
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Get user_id for timezone formatting
    user_id = request.session.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

    # Get page_size from user settings, fallback to global settings
    page_size = 50  # Default
    
    if user_id:
        user_result = query("SELECT page_size FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user_result and user_result["page_size"]:
            page_size = user_result["page_size"]
    
    if not user_id or not page_size:
        global_result = query("SELECT value FROM settings WHERE key = 'page_size'").mappings().first()
        if global_result:
            page_size = int(global_result["value"])

    page_size = min(max(10, page_size), 500)  # Ensure between 10-500
    offset = (page - 1) * page_size

    where = []
    params = {}

    if q:
        where.append(
            "to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(sender,'') || ' ' || coalesce(recipients,'')) @@ plainto_tsquery(:q)"
        )
        params["q"] = q

    if account:
        where.append("source = :account")
        params["account"] = account

    if folder:
        where.append("folder = :folder")
        params["folder"] = folder

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    rows = query(
        f"""
        SELECT id, source, folder, uid, subject, sender, recipients, date, created_at,
               virus_scanned, virus_detected, virus_name, raw_email, compressed, signature
        FROM emails
        WHERE quarantined = FALSE
        {where_sql}
        ORDER BY date DESC
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    total = query(
        f"SELECT COUNT(*) AS c FROM emails WHERE quarantined = FALSE {where_sql}",
        params,
    ).mappings().first()["c"]

    msg = request.session.pop("flash", None)

    # Compute integrity per-row (may be expensive because it needs raw bytes)
    processed = []
    for r in rows:
        rr = dict(r)
        integrity = "unknown"
        try:
            stored_sig = rr.get("signature")
            raw_blob = rr.get("raw_email")
            compressed_flag = rr.get("compressed")
            if raw_blob is not None:
                raw = decompress(raw_blob, compressed_flag)
                try:
                    current_sig = compute_signature(raw)
                except Exception:
                    current_sig = None

                if stored_sig is None:
                    integrity = "no_signature"
                elif current_sig is None:
                    integrity = "unknown"
                elif stored_sig == current_sig:
                    integrity = "ok"
                else:
                    integrity = "modified"
            else:
                integrity = "no_raw"
        except Exception:
            integrity = "unknown"

        # Format email date according to user preferences
        if rr["date"]:
            if hasattr(rr["date"], 'strftime'):  # Check if it's a datetime object
                rr["date_formatted"] = format_datetime(rr["date"], user_id)
            else:
                # Try to parse the date string
                try:
                    parsed_date = parsedate_to_datetime(rr["date"])
                    rr["date_formatted"] = format_datetime(parsed_date, user_id)
                except (ValueError, TypeError):
                    # If parsing fails, keep the original string
                    rr["date_formatted"] = rr["date"]
        else:
            rr["date_formatted"] = rr["date"]  # Keep as None/empty if already None

        # remove large raw fields before sending to template
        rr.pop("raw_email", None)
        rr.pop("compressed", None)
        rr["integrity"] = integrity
        processed.append(rr)

    return templates.TemplateResponse(
        "emails.html",
        {
            "request": request,
            "emails": processed,
            "page": page,
            "page_size": page_size,
            "total": total,
            "q": q or "",
            "account": account or "",
            "folder": folder or "",
            "flash": msg,
        },
    )


@router.get("/emails/import", response_class=HTMLResponse)
def emails_transfer_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("import_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to import emails", status_code=403)

    msg = request.session.pop("flash", None)
    # Provide list of fetch accounts so user can assign imported messages to a source
    try:
        accounts_rows = query("SELECT name FROM fetch_accounts ORDER BY name").mappings().all()
        accounts = [r["name"] for r in accounts_rows]
    except Exception:
        accounts = []

    return templates.TemplateResponse(
        "emails-transfer.html",
        {"request": request, "flash": msg, "accounts": accounts},
    )


def _insert_raw_email(raw: bytes, request: Request, source: str = "import", folder: str = "INBOX") -> bool:
    try:
        # Parse headers for metadata
        parsed = parse_email(raw)

        # Compute next uid for this source/folder to avoid collisions
        next_uid_row = query(
            "SELECT COALESCE(MAX(uid), 0) + 1 AS next_uid FROM emails WHERE source = :source AND folder = :folder",
            {"source": source, "folder": folder},
        ).mappings().first()
        uid = int(next_uid_row["next_uid"]) if next_uid_row else 1

        compressed_raw = gzip.compress(raw)
        try:
            sig = compute_signature(raw)
        except Exception:
            sig = None

        # Virus scanning
        virus_scanned = False
        virus_detected = False
        virus_name = None
        scan_timestamp = None

        scanner = ClamAVScanner()
        if scanner.is_enabled():
            virus_detected, virus_name, scan_timestamp = scanner.scan(raw)
            virus_scanned = True

            if virus_detected:
                username = request.session.get("username", "unknown")
                log("warning", "Import", f"Virus detected in imported email: {virus_name}", f"User: {username}, Source: {source}, Folder: {folder}")

                create_alert(
                    'error',
                    'Virus Detected in Imported Email',
                    f'Malicious email detected during import: {virus_name}',
                    f"""Virus: {virus_name}
Subject: {parsed["headers"].get("subject", "N/A")}
From: {parsed["headers"].get("from", "Unknown")}
Imported by: {username}
Source: {source}
Folder: {folder}""",
                    'virus_detected',
                )

                # Reject infected emails during import
                return False

        execute(
            """
            INSERT INTO emails (source, folder, uid, subject, sender, recipients, date, raw_email, signature, compressed, virus_scanned, virus_detected, virus_name, scan_timestamp)
            VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :raw_email, :signature, :compressed, :virus_scanned, :virus_detected, :virus_name, :scan_timestamp)
            """,
            {
                "source": source,
                "folder": folder,
                "uid": uid,
                "subject": parsed["headers"].get("subject", ""),
                "sender": parsed["headers"].get("from", ""),
                "recipients": parsed["headers"].get("to", ""),
                "date": parsed["headers"].get("date", ""),
                "raw_email": compressed_raw,
                "signature": sig,
                "compressed": True,
                "virus_scanned": virus_scanned,
                "virus_detected": virus_detected,
                "virus_name": virus_name,
                "scan_timestamp": scan_timestamp,
            },
        )

        username = request.session.get("username", "unknown")
        log("info", "Import", f"User '{username}' imported an email (source={source}, folder={folder}, uid={uid})", "")
        return True
    except Exception as e:
        log("error", "Import", f"Failed to insert imported email: {str(e)}", "")
        return False


@router.post("/emails/import")
async def import_emails(request: Request, source: str = Form("import"), folder: str = Form("INBOX"), files: List[UploadFile] = File(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("import_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to import emails", status_code=403)

    imported = 0
    errors = []

    for upload in files:
        filename = upload.filename or ""
        lower = filename.lower()
        content = await upload.read()

        try:
            if lower.endswith(".eml") or upload.content_type == "message/rfc822":
                if _insert_raw_email(content, request, source=source, folder=folder):
                    imported += 1
                else:
                    errors.append(f"{filename}: failed to insert")

            elif lower.endswith(".mbox") or lower.endswith(".mbx") or upload.content_type == "application/mbox":
                # Use mailbox to parse mbox content from memory
                try:
                    mbox = mailbox.mbox(io.BytesIO(content))
                except Exception:
                    # mailbox.mbox expects a file path; fallback to manual parse
                    buf = io.BytesIO(content)
                    data = buf.getvalue().split(b"\nFrom ")
                    for i, part in enumerate(data):
                        if not part.strip():
                            continue
                        # Ensure proper 'From ' prefix for the first part
                        raw = part if part.startswith(b"From ") else b"From " + part
                        if _insert_raw_email(raw, request, source=source, folder=folder):
                            imported += 1
                        else:
                            errors.append(f"{filename} part {i}: failed to insert")

            elif lower.endswith('.zip') or upload.content_type == 'application/zip':
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            try:
                                inner_name = info.filename
                                inner_lower = inner_name.lower()
                                fcontent = zf.read(info.filename)
                                if inner_lower.endswith('.eml') or inner_name.endswith('.msg'):
                                    if _insert_raw_email(fcontent, request, source=source, folder=folder):
                                        imported += 1
                                    else:
                                        errors.append(f"{filename}:{inner_name}: failed to insert")
                                elif inner_lower.endswith('.mbox') or inner_lower.endswith('.mbx'):
                                    # parse embedded mbox
                                    parts = fcontent.split(b"\nFrom ")
                                    for i, part in enumerate(parts):
                                        if not part.strip():
                                            continue
                                        raw = part if part.startswith(b"From ") else b"From " + part
                                        if _insert_raw_email(raw, request, source=source, folder=folder):
                                            imported += 1
                                        else:
                                            errors.append(f"{filename}:{inner_name} part {i}: failed to insert")
                                elif inner_lower.endswith('.pst'):
                                    # PST support removed; skip
                                    continue
                                else:
                                    # unsupported inner file, skip
                                    continue
                            except Exception as e:
                                errors.append(f"{filename}:{info.filename}: {str(e)}")
                except Exception as e:
                    errors.append(f"{filename}: zip extraction failed ({str(e)})")

            elif lower.endswith(".pst"):
                # PST support removed — do not attempt to parse PST files
                errors.append(f"{filename}: PST files are not supported")

            else:
                errors.append(f"{filename}: unsupported file type")
        except Exception as e:
            errors.append(f"{filename}: {str(e)}")

    if imported > 0:
        flash(request, f"Imported {imported} message(s).", 'success')
    if errors:
        flash(request, "; ".join(errors), 'error')

    return RedirectResponse("/emails", status_code=303)


@router.get("/emails/{email_id}", response_class=HTMLResponse)
def view_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    row = query(
        """
        SELECT id, source, folder, uid, subject, sender, recipients, date, message_id,
               raw_email, compressed, signature, created_at, virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined
        FROM emails
        WHERE id = :id
        """,
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Email not found", status_code=404)

    # Get user_id for timezone formatting
    user_id = request.session.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

    # Format timestamps according to user preferences
    row = dict(row)  # Convert to dict to make it mutable
    
    # Format scan_timestamp
    if row["scan_timestamp"]:
        row["scan_timestamp_formatted"] = format_datetime(row["scan_timestamp"], user_id)
    else:
        row["scan_timestamp_formatted"] = None
    
    # Format email date if it's a datetime object
    if row["date"]:
        if hasattr(row["date"], 'strftime'):  # Check if it's a datetime object
            row["date_formatted"] = format_datetime(row["date"], user_id)
        else:
            # Try to parse the date string
            try:
                parsed_date = parsedate_to_datetime(row["date"])
                row["date_formatted"] = format_datetime(parsed_date, user_id)
            except (ValueError, TypeError):
                # If parsing fails, keep the original string
                row["date_formatted"] = row["date"]
    else:
        row["date_formatted"] = row["date"]  # Keep as None/empty if already None

    raw = decompress(row["raw_email"], row["compressed"])
    # Ensure bytes type for parsing and preview
    if isinstance(raw, memoryview):
        raw = raw.tobytes()
    preview = raw[:10000].decode(errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw)
    parsed = parse_email(raw)

    # compute integrity status
    try:
        stored_sig = row.get("signature")
        current_sig = compute_signature(raw)
        if stored_sig is None:
            integrity = "no_signature"
        elif stored_sig == current_sig:
            integrity = "ok"
        else:
            integrity = "modified"
    except Exception:
        integrity = "unknown"

    username = request.session.get("username", "unknown")
    log("info", "Emails", f"User '{username}' viewed email ID {email_id}", "")

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "email-view.html",
        {
            "request": request,
            "email": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
            "preview": preview,
            "flash": msg,
            "integrity": integrity,
            "stored_signature": row.get("signature"),
            "current_signature": current_sig if 'current_sig' in locals() else None,
        },
    )


@router.get("/emails/{email_id}/download")
def download_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Check if user has permission to export emails
    checker = PermissionChecker(request)
    if not checker.has_permission("export_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to download email files", status_code=403)

    row = query(
        """
        SELECT raw_email, compressed
        FROM emails
        WHERE id = :id
        """,
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])

    username = request.session.get("username", "unknown")
    log("info", "Emails", f"User '{username}' downloaded email ID {email_id}", "")

    return StreamingResponse(
        iter([raw]),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="email-{email_id}.eml"'},
    )


@router.get("/emails/{email_id}/verify")
def verify_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Check permission to view emails
    checker = PermissionChecker(request)
    if not checker.has_permission("view_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to verify emails", status_code=403)

    row = query(
        "SELECT raw_email, compressed, signature FROM emails WHERE id = :id",
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"]) if row["raw_email"] is not None else b""
    current_sig = None
    try:
        current_sig = compute_signature(raw)
    except Exception:
        current_sig = None

    stored_sig = row.get("signature")

    match = (stored_sig is not None and current_sig is not None and stored_sig == current_sig)

    # Return JSON result
    from fastapi.responses import JSONResponse
    return JSONResponse({"id": email_id, "match": match, "stored_signature": stored_sig, "current_signature": current_sig})


@router.post("/emails/{email_id}/quarantine")
def quarantine_single_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):
        flash(request, "You don't have permission to quarantine emails.", 'error')
        return RedirectResponse(f"/emails/{email_id}", status_code=303)

    quarantined = _quarantine_emails([email_id], request.session.get("username", "unknown"))
    
    username = request.session.get("username", "unknown")
    log("warning", "Emails", f"User '{username}' quarantined 1 email (ID: {email_id})", "")
    
    # Create alert for security monitoring
    try:
        create_alert(
            'warning',
            'Email Quarantined',
            f'User {username} quarantined an email',
            f'Email ID: {email_id}',
            'email_quarantined'
        )
    except Exception as e:
        log("error", "Emails", f"Failed to create quarantine alert: {str(e)}", "")
    
    if quarantined > 0:
        flash(request, "Email quarantined successfully.", 'success')
    else:
        flash(request, "Email could not be quarantined (may already be quarantined).", 'error')
    
    return RedirectResponse("/emails", status_code=303)


@router.post("/emails/delete")
def perform_delete(
    request: Request,
    ids: List[int] = Form(...),
    mode: str = Form(...),  # "db" or "imap"
):
    """
    Perform the actual delete, either:
    - Database Only
    - Database and Mail Server (IMAP/Gmail/O365)
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):
        flash(request, "You don't have permission to delete emails.", 'error')
        return RedirectResponse("/emails", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = _delete_emails_from_db(ids)
        username = request.session.get("username", "unknown")
        log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from database (IDs: {ids})", "")
        flash(request, f"Deleted {deleted} email(s) from the database.", 'success')
        return RedirectResponse("/emails", status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_emails_from_mail_server_and_db(ids)

        username = request.session.get("username", "unknown")
        if errors:
            error_text = " | ".join(errors)
            log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from IMAP and database with errors (IDs: {ids})", error_text)
            flash(
                request,
                f"Deleted {deleted} email(s) from database and mail server. Some errors occurred: {error_text}",
                'error'
            )
        else:
            log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from IMAP and database (IDs: {ids})", "")
            flash(
                request,
                f"Deleted {deleted} email(s) from database and mail server.",
                'success'
            )

        return RedirectResponse("/emails", status_code=303)

    else:
        flash(request, "Invalid delete mode selected.", 'error')
        return RedirectResponse("/emails", status_code=303)


@router.post("/emails/quarantine")
def perform_quarantine(
    request: Request,
    ids: List[int] = Form(...),
):
    """
    Quarantine selected emails.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):  # Assuming quarantine requires same permission as delete
        flash(request, "You don't have permission to quarantine emails.", 'error')
        return RedirectResponse("/emails", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    quarantined = _quarantine_emails(ids, request.session.get("username", "unknown"))
    
    username = request.session.get("username", "unknown")
    log("warning", "Emails", f"User '{username}' quarantined {quarantined} email(s) (IDs: {ids})", "")
    
    # Create alert for security monitoring
    try:
        create_alert(
            'warning',
            'Emails Quarantined',
            f'User {username} quarantined {quarantined} email(s)',
            f'Email IDs: {ids}',
            'email_quarantined'
        )
    except Exception as e:
        log("error", "Emails", f"Failed to create quarantine alert: {str(e)}", "")
    
    if quarantined > 0:
        flash(request, f"Quarantined {quarantined} email(s).", 'success')
    else:
        flash(request, "No emails were quarantined.", 'info')
    
    return RedirectResponse("/emails", status_code=303)


def _quarantine_emails(ids: List[int], quarantined_by: str) -> int:
    """Quarantine emails by moving them to quarantined_emails table. Returns number quarantined."""
    if not ids:
        return 0
    
    quarantined_count = 0
    
    for email_id in ids:
        try:
            # Get email data
            email = query("""
                SELECT id, source, folder, uid, subject, sender, recipients, date, message_id, raw_email, compressed
                FROM emails 
                WHERE id = :id AND quarantined = FALSE
            """, {"id": email_id}).mappings().first()
            
            if not email:
                continue  # Already quarantined or doesn't exist
            
            # Use stored message_id
            message_id = email.get("message_id")
            
            # Insert into quarantined_emails
            quarantine_id = execute("""
                INSERT INTO quarantined_emails
                (original_source, original_folder, original_uid, subject, sender, recipients, date, message_id, raw_email, signature, compressed, reason, quarantined_by)
                VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :message_id, :raw_email, :signature, :compressed, :reason, :quarantined_by)
            """, {
                "source": email["source"],
                "folder": email["folder"],
                "uid": email["uid"],
                "subject": email["subject"],
                "sender": email["sender"],
                "recipients": email["recipients"],
                "date": email["date"],
                "message_id": message_id,
                "raw_email": email["raw_email"],
                "signature": email.get("signature"),
                "compressed": email["compressed"],
                "reason": "Manually Quarantined",
                "quarantined_by": quarantined_by
            })
            
            # Update emails table
            execute("""
                DELETE FROM emails WHERE id = :id
            """, {"id": email_id})
            
            quarantined_count += 1
            
        except Exception as e:
            log("error", "Emails", f"Failed to quarantine email ID {email_id}: {str(e)}", "")
    
    return quarantined_count


def _delete_emails_from_db(ids: List[int]) -> int:
    """Delete emails from the database only. Returns number of emails deleted."""
    if not ids:
        return 0
    
    # Delete all at once for better performance
    placeholders = ",".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": email_id for i, email_id in enumerate(ids)}
    
    result = query(
        f"DELETE FROM emails WHERE id IN ({placeholders}) RETURNING id",
        params,
    )
    deleted = len(result.all())
    
    # Track deletion statistics
    if deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'manual', :count, FALSE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": deleted},
        )
    
    return deleted


def _delete_emails_from_mail_server_and_db(ids: List[int]) -> tuple[int, list[str]]:
    """
    Delete emails from mail server (IMAP/Gmail/O365) and then from DB.
    Returns (deleted_count, errors).
    """
    errors: list[str] = []
    deleted = 0

    for mid in ids:
        email_row = query(
            """
            SELECT id, source, folder, uid
            FROM emails
            WHERE id = :id
            """,
            {"id": mid},
        ).mappings().first()

        if not email_row:
            errors.append(f"Email {mid} not found")
            continue

        account = query(
            """
            SELECT name, host, port, username, password_encrypted,
                   use_ssl, require_starttls
            FROM fetch_accounts
            WHERE name = :name
            """,
            {"name": email_row["source"]},
        ).mappings().first()

        if not account:
            errors.append(f"No fetch account found for source '{email_row['source']}' (email {mid})")
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
            folder = email_row["folder"]
            conn.select(folder)

            uid_str = str(email_row["uid"])
            typ, _ = conn.uid("STORE", uid_str, "+FLAGS", r"(\Deleted)")
            if typ != "OK":
                raise RuntimeError(f"Failed to flag email {mid} for deletion on mail server")

            typ, _ = conn.expunge()
            if typ != "OK":
                raise RuntimeError(f"Failed to expunge email {mid} on mail server")

            # Only delete from DB if IMAP delete succeeded
            query(
                "DELETE FROM emails WHERE id = :id",
                {"id": mid},
            )
            deleted += 1

        except Exception as e:
            errors.append(f"Email {mid}: {str(e)}")
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
            VALUES (CURRENT_DATE, 'manual', :count, TRUE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": deleted},
        )

    return deleted, errors


    # import routes moved earlier to avoid collision with /emails/{email_id}




@router.post("/emails/export")
def export_emails(request: Request, q: str = Form(None), account: str = Form(None), folder: str = Form(None), format: str = Form("zip")):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("export_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to export emails", status_code=403)

    # Build WHERE clause from filters
    where = []
    params = {}
    if q:
        where.append("to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(sender,'') || ' ' || coalesce(recipients,'')) @@ plainto_tsquery(:q)")
        params["q"] = q
    if account:
        where.append("source = :account")
        params["account"] = account
    if folder:
        where.append("folder = :folder")
        params["folder"] = folder

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    # Always exclude quarantined messages in export by default
    if where_sql:
        where_sql = where_sql + " AND quarantined = FALSE"
    else:
        where_sql = "WHERE quarantined = FALSE"

    rows = query(f"SELECT id, raw_email, compressed FROM emails {where_sql}", params).mappings().all()

    if not rows:
        flash(request, "No emails found to export.", 'error')
        return RedirectResponse("/emails", status_code=303)

    username = request.session.get("username", "unknown")

    if format == "mbox":
        mbox_buf = io.BytesIO()
        for r in rows:
            raw = decompress(r["raw_email"], r["compressed"]) if r["raw_email"] is not None else b""
            try:
                parsed = parse_email(raw)
                date_hdr = parsed.get("headers", {}).get("date", "-")
            except Exception:
                date_hdr = "-"

            from_line = f"From - {date_hdr}\n".encode("utf-8", errors="replace")
            mbox_buf.write(from_line)
            mbox_buf.write(raw)
            if not raw.endswith(b"\n"):
                mbox_buf.write(b"\n")
            mbox_buf.write(b"\n")

        mbox_buf.seek(0)
        log("info", "Export", f"User '{username}' exported {len(rows)} email(s) as mbox", "")
        return StreamingResponse(
            iter([mbox_buf.getvalue()]),
            media_type="application/mbox",
            headers={"Content-Disposition": f'attachment; filename="emails-export.mbox"'},
        )

    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in rows:
            raw = decompress(r["raw_email"], r["compressed"]) if r["raw_email"] is not None else b""
            zf.writestr(f"email-{r['id']}.eml", raw)

    bio.seek(0)
    log("info", "Export", f"User '{username}' exported {len(rows)} email(s)", "")

    return StreamingResponse(
        iter([bio.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="emails-export.zip"'},
    )
