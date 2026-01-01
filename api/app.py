import os
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text
from mailparser import parse_from_bytes

DB_DSN = os.getenv("DB_DSN")
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data/mail")

engine = create_engine(DB_DSN, future=True)

app = FastAPI()

templates = Jinja2Templates(directory="templates")


def build_search_where(
    q: str | None,
    account: str | None,
    folder: str | None,
    date_from: str | None,
    date_to: str | None,
    use_fts: bool,
) -> tuple[str, Dict[str, Any]]:
    clauses: List[str] = []
    params: Dict[str, Any] = {}

    # Basic / advanced search filters
    if account:
        clauses.append("account = :account")
        params["account"] = account

    if folder:
        clauses.append("folder = :folder")
        params["folder"] = folder

    if date_from:
        clauses.append("date >= :date_from")
        params["date_from"] = date_from

    if date_to:
        clauses.append("date <= :date_to")
        params["date_to"] = date_to

    # Text search (basic LIKE or full-text)
    if q:
        if use_fts:
            clauses.append("search_vector @@ plainto_tsquery('simple', :q)")
            params["q"] = q
        else:
            clauses.append(
                "(subject ILIKE :q_like "
                "OR sender ILIKE :q_like "
                "OR recipients ILIKE :q_like)"
            )
            params["q_like"] = f"%{q}%"

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    return where_sql, params


def build_order_by(sort: str | None, direction: str | None) -> str:
    sort = (sort or "date").lower()
    direction = (direction or "desc").lower()

    sort_map = {
        "date": "date",
        "created": "created_at",
        "sender": "sender",
        "subject": "subject",
        "folder": "folder",
        "id": "id",
    }

    col = sort_map.get(sort, "date")
    dir_sql = "ASC" if direction == "asc" else "DESC"

    return f"ORDER BY {col} {dir_sql}"


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/messages")


@app.get("/messages", response_class=HTMLResponse)
def list_messages(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    q: str | None = None,
    sort: str | None = None,
    direction: str | None = None,
    account: str | None = None,
    folder: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    use_fts: int = 0,
):
    if page < 1:
        page = 1
    offset = (page - 1) * page_size

    use_fts_bool = use_fts == 1

    where_sql, where_params = build_search_where(
        q=q,
        account=account,
        folder=folder,
        date_from=date_from,
        date_to=date_to,
        use_fts=use_fts_bool,
    )
    order_by_sql = build_order_by(sort, direction)

    query_params = dict(where_params)
    query_params["limit"] = page_size
    query_params["offset"] = offset

    list_sql = f"""
        SELECT id, account, folder, uid, subject, sender, recipients, date, created_at
        FROM messages
        {where_sql}
        {order_by_sql}
        LIMIT :limit OFFSET :offset
    """

    count_sql = f"""
        SELECT COUNT(*) AS c
        FROM messages
        {where_sql}
    """

    with engine.begin() as conn:
        rows = conn.execute(text(list_sql), query_params).mappings().all()
        total = conn.execute(text(count_sql), where_params).mappings().first()["c"]

        # For filter dropdowns: list distinct accounts/folders
        accounts = conn.execute(
            text("SELECT DISTINCT account FROM messages ORDER BY account")
        ).scalars().all()
        folders = conn.execute(
            text("SELECT DISTINCT folder FROM messages ORDER BY folder")
        ).scalars().all()

    has_next = (page * page_size) < total
    has_prev = page > 1

    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "messages": rows,
            "page": page,
            "page_size": page_size,
            "has_next": has_next,
            "has_prev": has_prev,
            "total": total,
            "q": q or "",
            "sort": sort or "date",
            "direction": direction or "desc",
            "account": account or "",
            "folder": folder or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
            "use_fts": use_fts_bool,
            "accounts": accounts,
            "folders": folders,
        },
    )


@app.get("/messages/{message_id}", response_class=HTMLResponse)
def view_message(request: Request, message_id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, account, folder, uid, subject, sender, recipients, date, storage_path, created_at
                FROM messages
                WHERE id = :id
                """
            ),
            {"id": message_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    storage_path = row["storage_path"]
    if not storage_path or not os.path.exists(storage_path):
        raise HTTPException(status_code=404, detail="Stored message file not found")

    with open(storage_path, "rb") as f:
        raw = f.read()

    parsed = parse_from_bytes(raw)

    body_text = parsed.text_plain[0] if parsed.text_plain else parsed.body

    attachments = [
        {
            "filename": att.get("filename"),
            "content_type": att.get("mail_content_type"),
            "size": len(att.get("payload", b"") or b""),
        }
        for att in parsed.attachments or []
    ]

    return templates.TemplateResponse(
        "message.html",
        {
            "request": request,
            "message": row,
            "body_text": body_text,
            "attachments": attachments,
        },
    )


@app.post("/messages/{message_id}/delete")
def delete_message(message_id: int):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT storage_path FROM messages WHERE id = :id"),
            {"id": message_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    storage_path = row["storage_path"]

    if storage_path and os.path.exists(storage_path):
        try:
            os.remove(storage_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")

    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM messages WHERE id = :id"),
            {"id": message_id},
        )

    return RedirectResponse(url="/messages", status_code=303)


@app.get("/api/messages")
def api_list_messages(
    limit: int = 100,
    q: str | None = None,
):
    where_sql, where_params = build_search_where(
        q=q,
        account=None,
        folder=None,
        date_from=None,
        date_to=None,
        use_fts=False,
    )

    list_sql = f"""
        SELECT id, account, folder, uid, subject, sender, recipients, date, created_at
        FROM messages
        {where_sql}
        ORDER BY date DESC
        LIMIT :limit
    """

    params = dict(where_params)
    params["limit"] = limit

    with engine.begin() as conn:
        rows = conn.execute(text(list_sql), params).mappings().all()
        return list(rows)