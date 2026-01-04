from typing import List

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from imaplib import IMAP4, IMAP4_SSL

from utils.db import query
from utils.email_parser import decompress, parse_email
from utils.security import decrypt_password, can_delete
from utils.logger import log

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str):
    request.session["flash"] = message


@router.get("/emails", response_class=HTMLResponse)
def list_emails(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    account: str | None = None,
    folder: str | None = None,
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

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
        SELECT id, source, folder, uid, subject, sender, recipients, date, created_at
        FROM emails
        {where_sql}
        ORDER BY date DESC
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    total = query(
        f"SELECT COUNT(*) AS c FROM emails {where_sql}",
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
               raw_email, compressed, created_at
        FROM emails
        WHERE id = :id
        """,
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Email not found", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])
    parsed = parse_email(raw)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "email_view.html",
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


@router.post("/emails/delete/confirm", response_class=HTMLResponse)
def confirm_bulk_delete(
    request: Request,
    ids: List[int] = Form(...),
    return_url: str = Form("/emails"),
):
    """
    Bulk delete confirmation for multiple emails.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):
        flash(request, "You don't have permission to delete emails.")
        return RedirectResponse(return_url or "/emails", status_code=303)

    # Ensure ids is a list (FastAPI will usually do this for multiple checkboxes)
    if not isinstance(ids, list):
        ids = [ids]

    count = len(ids)

    return templates.TemplateResponse(
        "emails_confirm_delete.html",
        {
            "request": request,
            "ids": ids,
            "count": count,
            "return_url": return_url,
        },
    )


@router.post("/emails/{email_id}/delete/confirm", response_class=HTMLResponse)
def confirm_single_delete(request: Request, email_id: int):
    """
    Single email delete confirmation.
    Reuses the same confirmation template as bulk delete.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if not can_delete(request):
        flash(request, "You don't have permission to delete emails.")
        return RedirectResponse(f"/emails/{email_id}", status_code=303)

    # Verify the email exists
    row = query(
        "SELECT id FROM emails WHERE id = :id",
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Email not found", status_code=404)

    ids = [email_id]
    count = 1

    return templates.TemplateResponse(
        "emails_confirm_delete.html",
        {
            "request": request,
            "ids": ids,
            "count": count,
            "return_url": f"/emails/{email_id}",
        },
    )


def _delete_emails_from_db(ids: List[int]) -> int:
    """Delete emails from the database only. Returns number of emails deleted."""
    deleted = 0
    for mid in ids:
        res = query(
            "DELETE FROM emails WHERE id = :id",
            {"id": mid},
        )
        deleted += 1
    
    # Track deletion statistics
    if deleted > 0:
        from utils.db import execute
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


def _delete_emails_from_imap_and_db(ids: List[int]) -> tuple[int, list[str]]:
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

            conn.logout()

            # Only delete from DB if IMAP delete succeeded
            query(
                "DELETE FROM emails WHERE id = :id",
                {"id": mid},
            )
            deleted += 1

        except Exception as e:
            errors.append(f"Email {mid}: {e}")

    # Track deletion statistics
    if deleted > 0:
        from utils.db import execute
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


@router.post("/emails/delete")
def perform_delete(
    request: Request,
    ids: List[int] = Form(...),
    mode: str = Form(...),  # "db" or "imap"
    return_url: str = Form("/emails"),
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
        return RedirectResponse(return_url or "/emails", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = _delete_emails_from_db(ids)
        username = request.session.get("username", "unknown")
        log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from database (IDs: {ids})", "")
        flash(request, f"Deleted {deleted} email(s) from the database.")
        return RedirectResponse("/emails", status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_emails_from_imap_and_db(ids)

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
        return RedirectResponse(return_url or "/emails", status_code=303)
