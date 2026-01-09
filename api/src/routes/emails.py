from typing import List

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from imaplib import IMAP4, IMAP4_SSL

from utils.db import query, execute
from utils.email_parser import decompress, parse_email
from utils.security import decrypt_password, can_delete
from utils.logger import log
from utils.templates import templates
from utils.timezone import format_datetime

router = APIRouter()


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str):
    request.session["flash"] = message


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

    # Get page_size from user settings, fallback to global settings
    user_id = request.session.get("user_id")
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
               virus_scanned, virus_detected, virus_name
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

    return templates.TemplateResponse(
        "emails.html",
        {
            "request": request,
            "emails": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "q": q or "",
            "account": account or "",
            "folder": folder or "",
            "flash": msg,
        },
    )


@router.get("/emails/{email_id}", response_class=HTMLResponse)
def view_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    row = query(
        """
        SELECT id, source, folder, uid, subject, sender, recipients, date,
               raw_email, compressed, created_at, virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined
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
    if row["date"] and hasattr(row["date"], 'strftime'):  # Check if it's a datetime object
        row["date_formatted"] = format_datetime(row["date"], user_id)
    else:
        row["date_formatted"] = row["date"]  # Keep as string if already formatted

    raw = decompress(row["raw_email"], row["compressed"])
    parsed = parse_email(raw)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "email-view.html",
        {
            "request": request,
            "email": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
            "flash": msg,
        },
    )


@router.get("/emails/{email_id}/download")
def download_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

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

    return StreamingResponse(
        iter([raw]),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="email-{email_id}.eml"'},
    )


@router.post("/emails/{email_id}/quarantine")
def quarantine_single_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):
        flash(request, "You don't have permission to quarantine emails.")
        return RedirectResponse(f"/emails/{email_id}", status_code=303)

    quarantined = _quarantine_emails([email_id], request.session.get("username", "unknown"))
    
    if quarantined > 0:
        flash(request, "Email quarantined successfully.")
    else:
        flash(request, "Email could not be quarantined (may already be quarantined).")
    
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
        flash(request, "You don't have permission to delete emails.")
        return RedirectResponse("/emails", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = _delete_emails_from_db(ids)
        username = request.session.get("username", "unknown")
        log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from database (IDs: {ids})", "")
        flash(request, f"Deleted {deleted} email(s) from the database.")
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
            )
        else:
            log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from IMAP and database (IDs: {ids})", "")
            flash(
                request,
                f"Deleted {deleted} email(s) from database and mail server.",
            )

        return RedirectResponse("/emails", status_code=303)

    else:
        flash(request, "Invalid delete mode selected.")
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
        flash(request, "You don't have permission to quarantine emails.")
        return RedirectResponse("/emails", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    quarantined = _quarantine_emails(ids, request.session.get("username", "unknown"))
    
    if quarantined > 0:
        flash(request, f"Quarantined {quarantined} email(s).")
    else:
        flash(request, "No emails were quarantined.")
    
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
                SELECT id, source, folder, uid, subject, sender, recipients, date, raw_email, compressed
                FROM emails 
                WHERE id = :id AND quarantined = FALSE
            """, {"id": email_id}).mappings().first()
            
            if not email:
                continue  # Already quarantined or doesn't exist
            
            # Insert into quarantined_emails
            quarantine_id = execute("""
                INSERT INTO quarantined_emails
                (original_source, original_folder, original_uid, subject, sender, recipients, date, raw_email, compressed, reason, quarantined_by)
                VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :raw_email, :compressed, :reason, :quarantined_by)
            """, {
                "source": email["source"],
                "folder": email["folder"],
                "uid": email["uid"],
                "subject": email["subject"],
                "sender": email["sender"],
                "recipients": email["recipients"],
                "date": email["date"],
                "raw_email": email["raw_email"],
                "compressed": email["compressed"],
                "reason": "manually quarantined",
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
    deleted = len(result.fetchall())
    
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
