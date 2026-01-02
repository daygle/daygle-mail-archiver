from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from utils.db import query
from utils.email_parser import decompress, parse_email

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    if request.session.get("user") is None:
        return False
    return True

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

    return templates.TemplateResponse(
        "view_message.html",
        {
            "request": request,
            "message": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
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