from typing import List

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from imaplib import IMAP4, IMAP4_SSL

from utils.db import query
from utils.email_parser import decompress, parse_email
from utils.security import decrypt_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str):
    request.session["flash"] = message


@router.get("/messages", response_class=HTMLResponse)
def list_messages(
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
        FROM messages
        {where_sql}
        ORDER BY date DESC
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    total = query(
        f"SELECT COUNT(*) AS c FROM messages {where_sql}",
        params,
    ).mappings().first()["c"]

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "messages": rows,
            "page": page,
            "page_size": page_size,
            "total": total,
            "q": q or "",
            "account": account or "",
            "folder": folder or "",
            "flash": msg,
        },
    )


@router.get("/messages/{message_id}", response_class=HTMLResponse)
def view_message(request: Request, message_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    row = query(
        """
        SELECT id, source, folder, uid, subject, sender, recipients, date,
               raw_email, compressed, created_at
        FROM messages
        WHERE id = :id
        """,
        {"id": message_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Message not found", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])
    parsed = parse_email(raw)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "message_view.html",
        {
            "request": request,
            "message": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
            "flash": msg,
        },
    )


@router.get("/messages/{message_id}/download")
def download_message(request: Request, message_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    row = query(
        """
        SELECT raw_email, compressed
        FROM messages
        WHERE id = :id
        """,
        {"id": message_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])

    return StreamingResponse(
        iter([raw]),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="message-{message_id}.eml"'},
    )


@router.post("/messages/delete/confirm", response_class=HTMLResponse)
def confirm_bulk_delete(
    request: Request,
    ids: List[int] = Form(...),
    return_url: str = Form("/messages"),
):
    """
    Bulk delete confirmation for multiple messages.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Ensure ids is a list (FastAPI will usually do this for multiple checkboxes)
    if not isinstance(ids, list):
        ids = [ids]

    count = len(ids)

    return templates.TemplateResponse(
        "messages_confirm_delete.html",
        {
            "request": request,
            "ids": ids,
            "count": count,
            "return_url": return_url,
        },
    )


@router.post("/messages/{message_id}/delete/confirm", response_class=HTMLResponse)
def confirm_single_delete(request: Request, message_id: int):
    """
    Single message delete confirmation.
    Reuses the same confirmation template as bulk delete.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Verify the message exists
    row = query(
        "SELECT id FROM messages WHERE id = :id",
        {"id": message_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Message not found", status_code=404)

    ids = [message_id]
    count = 1

    return templates.TemplateResponse(
        "messages_confirm_delete.html",
        {
            "request": request,
            "ids": ids,
            "count": count,
            "return_url": f"/messages/{message_id}",
        },
    )


def _delete_messages_from_db(ids: List[int]) -> int:
    """
    Delete messages from the database only.
    Returns number of messages deleted.
    """
    deleted = 0
    for mid in ids:
        res = query(
            "DELETE FROM messages WHERE id = :id",
            {"id": mid},
        )
        # res.rowcount may or may not be meaningful depending on driver, so we just increment optimistically
        deleted += 1
    return deleted


def _delete_messages_from_imap_and_db(ids: List[int]) -> tuple[int, list[str]]:
    """
    Delete messages from IMAP (based on source/folder/uid) and then from DB.
    Returns (deleted_count, errors).
    """
    errors: list[str] = []
    deleted = 0

    for mid in ids:
        msg_row = query(
            """
            SELECT id, source, folder, uid
            FROM messages
            WHERE id = :id
            """,
            {"id": mid},
        ).mappings().first()

        if not msg_row:
            errors.append(f"Message {mid} not found")
            continue

        account = query(
            """
            SELECT name, host, port, username, password_encrypted,
                   use_ssl, require_starttls
            FROM imap_accounts
            WHERE name = :name
            """,
            {"name": msg_row["source"]},
        ).mappings().first()

        if not account:
            errors.append(f"No IMAP account found for source '{msg_row['source']}' (message {mid})")
            continue

        try:
            # Connect to IMAP using same style as /imap_accounts/test
            if account["use_ssl"]:
                conn = IMAP4_SSL(account["host"], account["port"])
            else:
                conn = IMAP4(account["host"], account["port"])
                if account["require_starttls"]:
                    conn.starttls()

            password = decrypt_password(account["password_encrypted"])
            conn.login(account["username"], password)

            # Select the folder and delete by UID
            folder = msg_row["folder"]
            conn.select(folder)

            uid_str = str(msg_row["uid"])
            typ, _ = conn.uid("STORE", uid_str, "+FLAGS", r"(\Deleted)")
            if typ != "OK":
                raise RuntimeError(f"Failed to flag message {mid} for deletion on IMAP")

            typ, _ = conn.expunge()
            if typ != "OK":
                raise RuntimeError(f"Failed to expunge message {mid} on IMAP")

            conn.logout()

            # Only delete from DB if IMAP delete succeeded
            query(
                "DELETE FROM messages WHERE id = :id",
                {"id": mid},
            )
            deleted += 1

        except Exception as e:
            errors.append(f"Message {mid}: {e}")

    return deleted, errors


@router.post("/messages/delete")
def perform_delete(
    request: Request,
    ids: List[int] = Form(...),
    mode: str = Form(...),  # "db" or "imap"
    return_url: str = Form("/messages"),
):
    """
    Perform the actual delete, either:
    - Database Only
    - Database and IMAP
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = _delete_messages_from_db(ids)
        flash(request, f"Deleted {deleted} message(s) from the database.")
        return RedirectResponse("/messages", status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_messages_from_imap_and_db(ids)

        if errors:
            error_text = " | ".join(errors)
            flash(
                request,
                f"Deleted {deleted} message(s) from database and IMAP. Some errors occurred: {error_text}",
            )
        else:
            flash(
                request,
                f"Deleted {deleted} message(s) from database and IMAP.",
            )

        return RedirectResponse("/messages", status_code=303)

    else:
        flash(request, "Invalid delete mode selected.")
        return RedirectResponse(return_url or "/messages", status_code=303)
