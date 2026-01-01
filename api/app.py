import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text
from mailparser import parse_from_bytes

DB_DSN = os.getenv("DB_DSN")
STORAGE_DIR = os.getenv("STORAGE_DIR", "/data/mail")

engine = create_engine(DB_DSN, future=True)

app = FastAPI()

templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
def root():
    # Redirect to messages list
    return RedirectResponse(url="/messages")


@app.get("/messages", response_class=HTMLResponse)
def list_messages(request: Request, page: int = 1, page_size: int = 50):
    if page < 1:
        page = 1
    offset = (page - 1) * page_size

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, account, folder, uid, subject, sender, recipients, date, created_at
                FROM messages
                ORDER BY id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {"limit": page_size, "offset": offset},
        ).mappings().all()

        total = conn.execute(
            text("SELECT COUNT(*) AS c FROM messages")
        ).mappings().first()["c"]

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

    # Prefer plaintext body; fall back to full payload text
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


# Existing JSON API endpoint (kept for integrations)
@app.get("/api/messages")
def api_list_messages(limit: int = 100):
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, account, folder, uid, subject, sender, recipients, date, created_at
                FROM messages
                ORDER BY id DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
        return list(rows)