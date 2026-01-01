import os
import gzip
from datetime import datetime
from typing import Any, Dict, Tuple

from fastapi import (
    FastAPI,
    Request,
    Depends,
    Form,
    HTTPException,
    status,
)
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from cryptography.fernet import Fernet
from email import message_from_bytes
from email.message import Message as EmailMessage


# ------------------
# Configuration
# ------------------

DB_DSN = os.getenv("DB_DSN")
if not DB_DSN:
    raise RuntimeError("DB_DSN is not set")

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-session-secret")

# Used to encrypt IMAP passwords in imap_accounts
IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")
if not IMAP_PASSWORD_KEY:
    raise RuntimeError("IMAP_PASSWORD_KEY is not set")

# Simple admin auth (you can wire this to settings later if you like)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

engine: Engine = create_engine(DB_DSN, future=True)
fernet = Fernet(IMAP_PASSWORD_KEY.encode())

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ------------------
# Helpers: auth
# ------------------

def require_login(request: Request) -> bool:
    user = request.session.get("user")
    return user == ADMIN_USERNAME


def ensure_logged_in(request: Request) -> None:
    if not require_login(request):
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER)


# ------------------
# Helpers: settings
# ------------------

def load_settings() -> Dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT key, value FROM settings")).mappings().all()
        return {r["key"]: r["value"] for r in rows}


def save_setting(key: str, value: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO settings (key, value)
                VALUES (:key, :value)
                ON CONFLICT (key)
                DO UPDATE SET value = EXCLUDED.value
                """
            ),
            {"key": key, "value": value},
        )


# ------------------
# Helpers: IMAP accounts
# ------------------

def decrypt_password(token: str) -> str:
    return fernet.decrypt(token.encode()).decode()


def encrypt_password(plaintext: str) -> str:
    return fernet.encrypt(plaintext.encode()).decode()


# ------------------
# Helpers: search / ordering
# ------------------

def build_search_where(
    q: str | None,
    account: str | None,
    folder: str | None,
    date_from: str | None,
    date_to: str | None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Always use PostgreSQL FTS.

    We create a tsvector over subject, sender, recipients and match against plainto_tsquery.
    """
    conditions = []
    params: Dict[str, Any] = {}

    if q:
        conditions.append(
            "to_tsvector('simple', coalesce(subject, '') || ' ' || coalesce(sender, '') || ' ' || coalesce(recipients, '')) @@ plainto_tsquery(:q)"
        )
        params["q"] = q

    if account:
        conditions.append("source = :account")
        params["account"] = account

    if folder:
        conditions.append("folder = :folder")
        params["folder"] = folder

    if date_from:
        conditions.append("date >= :date_from")
        params["date_from"] = date_from

    if date_to:
        conditions.append("date <= :date_to")
        params["date_to"] = date_to

    where_sql = ""
    if conditions:
        where_sql = "WHERE " + " AND ".join(conditions)

    return where_sql, params


def build_order_by(sort: str | None, direction: str | None) -> str:
    allowed_sort = {
        "date": "date",
        "subject": "subject",
        "sender": "sender",
        "source": "source",
        "folder": "folder",
        "created_at": "created_at",
    }
    col = allowed_sort.get(sort or "date", "date")

    dir_normalized = (direction or "desc").lower()
    if dir_normalized not in ("asc", "desc"):
        dir_normalized = "desc"

    return f"ORDER BY {col} {dir_normalized}"


# ------------------
# Helpers: message parsing
# ------------------

def decompress_email(raw: bytes, compressed: bool) -> bytes:
    if compressed:
        return gzip.decompress(raw)
    return raw


def parse_email_structure(raw_email: bytes) -> Dict[str, Any]:
    """
    Parse raw email bytes into a structure suitable for templates.
    """
    msg: EmailMessage = message_from_bytes(raw_email)

    def get_body(msg: EmailMessage) -> Dict[str, str]:
        text_body = ""
        html_body = ""

        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "").lower()

                if "attachment" in disp:
                    continue

                if ctype == "text/plain":
                    try:
                        text_body += part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                    except Exception:
                        continue
                elif ctype == "text/html":
                    try:
                        html_body += part.get_payload(decode=True).decode(
                            part.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                    except Exception:
                        continue
        else:
            ctype = msg.get_content_type()
            if ctype == "text/plain":
                text_body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace",
                )
            elif ctype == "text/html":
                html_body = msg.get_payload(decode=True).decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace",
                )

        return {"text": text_body, "html": html_body}

    headers = {
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
    }

    body = get_body(msg)
    return {"headers": headers, "body": body}


# ------------------
# Routes: auth
# ------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": ""},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session["user"] = username
        return RedirectResponse(url="/messages", status_code=303)

    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password"},
    )


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ------------------
# Routes: messages
# ------------------

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
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    try:
        default_page_size = int(settings.get("page_size", "50"))
    except ValueError:
        default_page_size = 50

    if "page_size" not in request.query_params:
        page_size = default_page_size

    if page < 1:
        page = 1
    offset = (page - 1) * page_size

    where_sql, where_params = build_search_where(
        q=q,
        account=account,
        folder=folder,
        date_from=date_from,
        date_to=date_to,
    )
    order_by_sql = build_order_by(sort, direction)

    query_params = dict(where_params)
    query_params["limit"] = page_size
    query_params["offset"] = offset

    list_sql = f"""
        SELECT id, source, folder, uid, subject, sender, recipients, date, created_at
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

        accounts = conn.execute(
            text("SELECT DISTINCT source FROM messages ORDER BY source")
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
            "accounts": accounts,
            "folders": folders,
        },
    )


@app.get("/messages/{message_id}", response_class=HTMLResponse)
def view_message(request: Request, message_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = """
        SELECT id, source, folder, uid, subject, sender, recipients, date, raw_email, compressed, created_at
        FROM messages
        WHERE id = :id
    """

    with engine.begin() as conn:
        row = conn.execute(text(sql), {"id": message_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    raw_stored: bytes = row["raw_email"]
    compressed: bool = row["compressed"]
    raw_email = decompress_email(raw_stored, compressed)

    parsed = parse_email_structure(raw_email)

    return templates.TemplateResponse(
        "view_message.html",
        {
            "request": request,
            "message": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
        },
    )


@app.get("/messages/{message_id}/download")
def download_message(request: Request, message_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = """
        SELECT id, raw_email, compressed
        FROM messages
        WHERE id = :id
    """

    with engine.begin() as conn:
        row = conn.execute(text(sql), {"id": message_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Message not found")

    raw_stored: bytes = row["raw_email"]
    compressed: bool = row["compressed"]
    raw_email = decompress_email(raw_stored, compressed)

    filename = f"message-{message_id}.eml"

    return StreamingResponse(
        iter([raw_email]),
        media_type="message/rfc822",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


# ------------------
# Routes: settings
# ------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings},
    )


@app.post("/settings", response_class=HTMLResponse)
def settings_save(
    request: Request,
    page_size: int = Form(...),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    save_setting("page_size", str(page_size))

    return RedirectResponse(url="/settings", status_code=303)


# ------------------
# Routes: IMAP accounts
# ------------------

@app.get("/imap_accounts", response_class=HTMLResponse)
def list_imap_accounts(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = """
        SELECT
            id,
            name,
            host,
            port,
            username,
            use_ssl,
            require_starttls,
            poll_interval_seconds,
            delete_after_processing,
            enabled,
            last_heartbeat,
            last_success,
            last_error
        FROM imap_accounts
        ORDER BY id
    """
    with engine.begin() as conn:
        accounts = conn.execute(text(sql)).mappings().all()

    return templates.TemplateResponse(
        "imap_accounts.html",
        {
            "request": request,
            "accounts": accounts,
        },
    )


@app.get("/imap_accounts/new", response_class=HTMLResponse)
def new_imap_account_form(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "imap_account_form.html",
        {
            "request": request,
            "account": None,
        },
    )


@app.post("/imap_accounts/new", response_class=HTMLResponse)
def create_imap_account(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    use_ssl: bool = Form(False),
    require_starttls: bool = Form(False),
    poll_interval_seconds: int = Form(300),
    delete_after_processing: bool = Form(False),
    enabled: bool = Form(True),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    password_encrypted = encrypt_password(password)

    sql = """
        INSERT INTO imap_accounts
            (name, host, port, username, password_encrypted,
             use_ssl, require_starttls, poll_interval_seconds,
             delete_after_processing, enabled,
             last_heartbeat, last_success, last_error)
        VALUES
            (:name, :host, :port, :username, :password_encrypted,
             :use_ssl, :require_starttls, :poll_interval_seconds,
             :delete_after_processing, :enabled,
             NULL, NULL, NULL)
    """
    with engine.begin() as conn:
        conn.execute(
            text(sql),
            {
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "password_encrypted": password_encrypted,
                "use_ssl": use_ssl,
                "require_starttls": require_starttls,
                "poll_interval_seconds": poll_interval_seconds,
                "delete_after_processing": delete_after_processing,
                "enabled": enabled,
            },
        )

    return RedirectResponse(url="/imap_accounts", status_code=303)


@app.get("/imap_accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_imap_account_form(request: Request, account_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = """
        SELECT
            id,
            name,
            host,
            port,
            username,
            password_encrypted,
            use_ssl,
            require_starttls,
            poll_interval_seconds,
            delete_after_processing,
            enabled
        FROM imap_accounts
        WHERE id = :id
    """
    with engine.begin() as conn:
        account = conn.execute(text(sql), {"id": account_id}).mappings().first()

    if not account:
        raise HTTPException(status_code=404, detail="IMAP account not found")

    # We do not decrypt the password to show it; form should treat password as "change if non-empty".
    return templates.TemplateResponse(
        "imap_account_form.html",
        {
            "request": request,
            "account": account,
        },
    )


@app.post("/imap_accounts/{account_id}/edit", response_class=HTMLResponse)
def update_imap_account(
    request: Request,
    account_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    use_ssl: bool = Form(False),
    require_starttls: bool = Form(False),
    poll_interval_seconds: int = Form(300),
    delete_after_processing: bool = Form(False),
    enabled: bool = Form(True),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    with engine.begin() as conn:
        if password.strip():
            password_encrypted = encrypt_password(password)
            sql = """
                UPDATE imap_accounts
                SET name = :name,
                    host = :host,
                    port = :port,
                    username = :username,
                    password_encrypted = :password_encrypted,
                    use_ssl = :use_ssl,
                    require_starttls = :require_starttls,
                    poll_interval_seconds = :poll_interval_seconds,
                    delete_after_processing = :delete_after_processing,
                    enabled = :enabled
                WHERE id = :id
            """
            params = {
                "id": account_id,
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "password_encrypted": password_encrypted,
                "use_ssl": use_ssl,
                "require_starttls": require_starttls,
                "poll_interval_seconds": poll_interval_seconds,
                "delete_after_processing": delete_after_processing,
                "enabled": enabled,
            }
        else:
            sql = """
                UPDATE imap_accounts
                SET name = :name,
                    host = :host,
                    port = :port,
                    username = :username,
                    use_ssl = :use_ssl,
                    require_starttls = :require_starttls,
                    poll_interval_seconds = :poll_interval_seconds,
                    delete_after_processing = :delete_after_processing,
                    enabled = :enabled
                WHERE id = :id
            """
            params = {
                "id": account_id,
                "name": name,
                "host": host,
                "port": port,
                "username": username,
                "use_ssl": use_ssl,
                "require_starttls": require_starttls,
                "poll_interval_seconds": poll_interval_seconds,
                "delete_after_processing": delete_after_processing,
                "enabled": enabled,
            }

        conn.execute(text(sql), params)

    return RedirectResponse(url="/imap_accounts", status_code=303)


@app.post("/imap_accounts/{account_id}/delete")
def delete_imap_account(request: Request, account_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = "DELETE FROM imap_accounts WHERE id = :id"
    with engine.begin() as conn:
        conn.execute(text(sql), {"id": account_id})

    return RedirectResponse(url="/imap_accounts", status_code=303)


# ------------------
# Routes: error log
# ------------------

@app.get("/error_log", response_class=HTMLResponse)
def view_error_log(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    sql = """
        SELECT id, timestamp, source, message, details
        FROM error_log
        ORDER BY timestamp DESC
        LIMIT 200
    """
    with engine.begin() as conn:
        rows = conn.execute(text(sql)).mappings().all()

    return templates.TemplateResponse(
        "error_log.html",
        {
            "request": request,
            "errors": rows,
        },
    )


# ------------------
# Root redirect
# ------------------

@app.get("/")
def root(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/messages", status_code=303)