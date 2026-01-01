import os
import ssl
from typing import List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, text
from mailparser import parse_from_bytes
from starlette.middleware.sessions import SessionMiddleware
from cryptography.fernet import Fernet, InvalidToken
from imapclient import IMAPClient

DB_DSN = os.getenv("DB_DSN")
STORAGE_DIR_DEFAULT = os.getenv("STORAGE_DIR", "/data/mail")
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-session-secret")
IMAP_PASSWORD_KEY = os.getenv("IMAP_PASSWORD_KEY")

engine = create_engine(DB_DSN, future=True)

app = FastAPI()

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="daygle_session",
    max_age=60 * 60 * 8,
)

templates = Jinja2Templates(directory="templates")


# ------------------
# Encryption helpers for IMAP password
# ------------------

def get_fernet() -> Fernet | None:
    if not IMAP_PASSWORD_KEY:
        return None
    return Fernet(IMAP_PASSWORD_KEY.encode("utf-8"))


def encrypt_imap_password(plaintext: str) -> str:
    f = get_fernet()
    if not f:
        return ""
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_imap_password(token: str) -> str:
    if not token:
        return ""
    f = get_fernet()
    if not f:
        return ""
    try:
        plaintext = f.decrypt(token.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken:
        return ""


# ------------------
# Generic helpers
# ------------------

def require_login(request: Request) -> bool:
    return "user_id" in request.session


def load_settings() -> Dict[str, str]:
    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT key, value FROM settings")
        ).mappings().all()
    return {r["key"]: r["value"] for r in rows}


def save_settings(updates: Dict[str, str]) -> None:
    with engine.begin() as conn:
        for k, v in updates.items():
            conn.execute(
                text(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (:key, :value, NOW())
                    ON CONFLICT (key)
                    DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """
                ),
                {"key": k, "value": v},
            )


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


# ------------------
# Health / test helpers
# ------------------

def test_imap(settings: Dict[str, str]) -> tuple[bool, str]:
    host = settings.get("imap_host")
    port = int(settings.get("imap_port", "993"))
    user = settings.get("imap_user")
    use_ssl = settings.get("imap_use_ssl", "true").lower() == "true"
    require_starttls = settings.get("imap_require_starttls", "false").lower() == "true"
    ca_bundle = settings.get("imap_ca_bundle") or ""
    encrypted_pw = settings.get("imap_password_encrypted", "")
    password = decrypt_imap_password(encrypted_pw)

    if not host or not user or not password:
        return False, "IMAP host, user, or password is missing."

    def create_ssl_context():
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        if ca_bundle:
            ctx.load_verify_locations(ca_bundle)
        return ctx

    try:
        if use_ssl:
            ctx = create_ssl_context()
            client = IMAPClient(
                host,
                port=port,
                ssl=True,
                ssl_context=ctx,
                timeout=20,
            )
        else:
            client = IMAPClient(
                host,
                port=port,
                ssl=False,
                timeout=20,
            )
            if port == 143 and require_starttls:
                caps = client.capabilities()
                if b"STARTTLS" not in caps:
                    return False, "Server does not support STARTTLS."
                client.starttls(create_ssl_context())

        client.login(user, password)
        client.logout()
        return True, "Successfully connected and authenticated to IMAP server."
    except Exception as e:
        return False, f"IMAP test failed: {e}"


def test_storage(settings: Dict[str, str]) -> tuple[bool, str]:
    storage_dir = settings.get("storage_dir", STORAGE_DIR_DEFAULT)
    test_file = os.path.join(storage_dir, ".daygle_test.tmp")

    try:
        os.makedirs(storage_dir, exist_ok=True)
        with open(test_file, "w") as f:
            f.write("test")
        os.remove(test_file)
        return True, f"Storage directory is writable: {storage_dir}"
    except Exception as e:
        return False, f"Storage directory test failed for {storage_dir}: {e}"


def test_db() -> tuple[bool, str]:
    try:
        with engine.begin() as conn:
            # basic connectivity
            conn.execute(text("SELECT 1"))
            # check required tables
            for tbl in ("messages", "users", "settings", "worker_status"):
                conn.execute(text(f"SELECT 1 FROM {tbl} LIMIT 1"))
        return True, "Database connection and required tables are OK."
    except Exception as e:
        return False, f"DB test failed: {e}"


def get_worker_status() -> Dict[str, Any] | None:
    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, last_heartbeat, last_success, last_error,
                       last_run_duration_seconds, messages_processed
                FROM worker_status
                WHERE id = 1
                """
            )
        ).mappings().first()
    return dict(row) if row else None


def get_message_count() -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS c FROM messages")
        ).mappings().first()
    return row["c"] if row else 0


def get_storage_stats(settings: Dict[str, str]) -> Dict[str, Any]:
    storage_dir = settings.get("storage_dir", STORAGE_DIR_DEFAULT)
    exists = os.path.isdir(storage_dir)
    total_size = 0
    file_count = 0
    if exists:
        for root, dirs, files in os.walk(storage_dir):
            for name in files:
                fp = os.path.join(root, name)
                try:
                    total_size += os.path.getsize(fp)
                    file_count += 1
                except OSError:
                    continue
    return {
        "storage_dir": storage_dir,
        "exists": exists,
        "total_size_bytes": total_size,
        "file_count": file_count,
    }


# ------------------
# Root
# ------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/messages", status_code=303)


# ------------------
# Authentication
# ------------------

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if require_login(request):
        return RedirectResponse(url="/messages", status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    error = None

    if not username or not password:
        error = "Username and password are required."
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error},
        )

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id,
                       password_hash = crypt(:password, password_hash) AS valid
                FROM users
                WHERE username = :username
                """
            ),
            {"username": username, "password": password},
        ).mappings().first()

    if not row or not row["valid"]:
        error = "Invalid username or password."
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": error},
        )

    request.session["user_id"] = row["id"]
    request.session["username"] = username

    return RedirectResponse(url="/messages", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ------------------
# Settings UI + tests
# ------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_form(
    request: Request,
    imap_result: str | None = None,
    storage_result: str | None = None,
    db_result: str | None = None,
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "imap_result": imap_result,
            "storage_result": storage_result,
            "db_result": db_result,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
def settings_submit(
    request: Request,
    imap_host: str = Form(...),
    imap_port: str = Form(...),
    imap_user: str = Form(...),
    imap_password: str = Form(""),
    imap_use_ssl: str = Form("false"),
    imap_require_starttls: str = Form("false"),
    imap_ca_bundle: str = Form(""),
    poll_interval_seconds: str = Form(...),
    delete_after_processing: str = Form("false"),
    storage_dir: str = Form(...),
    page_size: str = Form(...),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    error = None

    try:
        int(imap_port)
    except ValueError:
        error = "IMAP port must be a number."

    try:
        int(poll_interval_seconds)
    except ValueError:
        error = (error + " " if error else "") + "Poll interval must be a number."

    try:
        int(page_size)
    except ValueError:
        error = (error + " " if error else "") + "Page size must be a number."

    if error:
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "settings": settings,
                "error": error,
                "success": None,
                "imap_result": None,
                "storage_result": None,
                "db_result": None,
            },
        )

    updates: Dict[str, str] = {
        "imap_host": imap_host.strip(),
        "imap_port": imap_port.strip(),
        "imap_user": imap_user.strip(),
        "imap_use_ssl": "true" if imap_use_ssl == "true" else "false",
        "imap_require_starttls": "true" if imap_require_starttls == "true" else "false",
        "imap_ca_bundle": imap_ca_bundle.strip(),
        "poll_interval_seconds": poll_interval_seconds.strip(),
        "delete_after_processing": "true" if delete_after_processing == "true" else "false",
        "storage_dir": storage_dir.strip(),
        "page_size": page_size.strip(),
    }

    if imap_password:
        encrypted = encrypt_imap_password(imap_password)
        updates["imap_password_encrypted"] = encrypted

    save_settings(updates)

    settings = load_settings()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": "Settings saved successfully.",
            "imap_result": None,
            "storage_result": None,
            "db_result": None,
        },
    )


@app.get("/settings/test-imap", response_class=HTMLResponse)
def settings_test_imap(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    ok, msg = test_imap(settings)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "imap_result": ("success" if ok else "error") + ":" + msg,
            "storage_result": None,
            "db_result": None,
        },
    )


@app.get("/settings/test-storage", response_class=HTMLResponse)
def settings_test_storage(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    ok, msg = test_storage(settings)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "imap_result": None,
            "storage_result": ("success" if ok else "error") + ":" + msg,
            "db_result": None,
        },
    )


@app.get("/settings/test-db", response_class=HTMLResponse)
def settings_test_db(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    ok, msg = test_db()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "imap_result": None,
            "storage_result": None,
            "db_result": ("success" if ok else "error") + ":" + msg,
        },
    )


# ------------------
# Status dashboard
# ------------------

@app.get("/status", response_class=HTMLResponse)
def status_page(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    worker = get_worker_status()
    msg_count = get_message_count()
    storage_stats = get_storage_stats(settings)

    imap_ok, imap_msg = test_imap(settings)
    storage_ok, storage_msg = test_storage(settings)
    db_ok, db_msg = test_db()

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "settings": settings,
            "worker": worker,
            "message_count": msg_count,
            "storage_stats": storage_stats,
            "imap_ok": imap_ok,
            "imap_msg": imap_msg,
            "storage_ok": storage_ok,
            "storage_msg": storage_msg,
            "db_ok": db_ok,
            "db_msg": db_msg,
        },
    )


@app.get("/api/status")
def api_status():
    settings = load_settings()
    worker = get_worker_status()
    msg_count = get_message_count()
    storage_stats = get_storage_stats(settings)
    imap_ok, imap_msg = test_imap(settings)
    storage_ok, storage_msg = test_storage(settings)
    db_ok, db_msg = test_db()

    return {
        "worker": worker,
        "message_count": msg_count,
        "storage": storage_stats,
        "tests": {
            "imap": {"ok": imap_ok, "message": imap_msg},
            "storage": {"ok": storage_ok, "message": storage_msg},
            "db": {"ok": db_ok, "message": db_msg},
        },
    }


# ------------------
# Messages UI
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
    use_fts: int = 0,
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
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

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
def delete_message(request: Request, message_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

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


# ------------------
# JSON API
# ------------------

@app.get("/api/messages")
def api_list_messages(
    request: Request,
    limit: int = 100,
    q: str | None = None,
):
    if not require_login(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

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