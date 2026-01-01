import os
import ssl
from typing import List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Form, Query
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
# Encryption helpers
# ------------------

def get_fernet() -> Fernet | None:
    if not IMAP_PASSWORD_KEY:
        return None
    return Fernet(IMAP_PASSWORD_KEY.encode("utf-8"))


def encrypt_password(plaintext: str) -> str:
    f = get_fernet()
    if not f:
        return ""
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_password(token: str) -> str:
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
        "account": "account",
    }

    col = sort_map.get(sort, "date")
    dir_sql = "ASC" if direction == "asc" else "DESC"

    return f"ORDER BY {col} {dir_sql}"


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


def get_message_count() -> int:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS c FROM messages")
        ).mappings().first()
    return row["c"] if row else 0


def get_accounts() -> list[dict]:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT id, name, host, port, username, use_ssl, require_starttls,
                       poll_interval_seconds, delete_after_processing,
                       enabled, last_heartbeat, last_success, last_error
                FROM imap_accounts
                ORDER BY name
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


# ------------------
# Tests / health
# ------------------

def test_imap_account(account_id: int) -> tuple[bool, str]:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT * FROM imap_accounts WHERE id = :id"),
            {"id": account_id},
        ).mappings().first()

    if not row:
        return False, "Account not found"

    host = row["host"]
    port = row["port"]
    user = row["username"]
    use_ssl = row["use_ssl"]
    require_starttls = row["require_starttls"]
    ca_bundle = row["ca_bundle"]
    password = decrypt_password(row["password_encrypted"])

    if not host or not user or not password:
        return False, "Host, username, or password is missing."

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
        return True, "Successfully connected and authenticated."
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
            conn.execute(text("SELECT 1"))
            for tbl in ("messages", "users", "settings", "imap_accounts", "error_log"):
                conn.execute(text(f"SELECT 1 FROM {tbl} LIMIT 1"))
        return True, "Database connection and required tables are OK."
    except Exception as e:
        return False, f"DB test failed: {e}"


def get_recent_errors(source_prefix: str | None = None, limit: int = 100) -> list[dict]:
    sql = """
        SELECT id, timestamp, source, message, details
        FROM error_log
    """
    params: Dict[str, Any] = {"limit": limit}
    if source_prefix:
        sql += " WHERE source LIKE :src"
        params["src"] = source_prefix + "%"
    sql += " ORDER BY timestamp DESC LIMIT :limit"

    with engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# ------------------
# Root / auth
# ------------------

@app.get("/", response_class=HTMLResponse)
def root(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)
    return RedirectResponse(url="/messages", status_code=303)


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
# Settings (global)
# ------------------

@app.get("/settings", response_class=HTMLResponse)
def settings_form(request: Request):
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
            "storage_result": None,
            "db_result": None,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
def settings_submit(
    request: Request,
    storage_dir: str = Form(...),
    page_size: str = Form(...),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    error = None

    # Validate page size
    try:
        int(page_size)
    except ValueError:
        error = "Page size must be a number."

    if error:
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "settings": settings,
                "error": error,
                "success": None,
                "storage_result": None,
                "db_result": None,
            },
        )

    updates: Dict[str, str] = {
        "storage_dir": storage_dir.strip(),
        "page_size": page_size.strip(),
    }

    save_settings(updates)
    settings = load_settings()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": "Settings saved successfully.",
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
    prefix = "success" if ok else "error"
    storage_result = f"{prefix}:{msg}"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "storage_result": storage_result,
            "db_result": None,
        },
    )


@app.get("/settings/test-db", response_class=HTMLResponse)
def settings_test_db(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    settings = load_settings()
    ok, msg = test_db()
    prefix = "success" if ok else "error"
    db_result = f"{prefix}:{msg}"

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "error": None,
            "success": None,
            "storage_result": None,
            "db_result": db_result,
        },
    )


# ------------------
# IMAP accounts management
# ------------------

@app.get("/accounts", response_class=HTMLResponse)
def accounts_list(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    accounts = get_accounts()
    return templates.TemplateResponse(
        "accounts.html",
        {
            "request": request,
            "accounts": accounts,
        },
    )


@app.get("/accounts/new", response_class=HTMLResponse)
def account_new_form(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "account_form.html",
        {
            "request": request,
            "account": None,
            "error": None,
            "success": None,
        },
    )


@app.post("/accounts/new", response_class=HTMLResponse)
def account_new_submit(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    use_ssl: str = Form("true"),
    require_starttls: str = Form("false"),
    ca_bundle: str = Form(""),
    poll_interval_seconds: int = Form(...),
    delete_after_processing: str = Form("true"),
    enabled: str = Form("true"),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    error = None
    use_ssl_bool = use_ssl == "true"
    require_starttls_bool = require_starttls == "true"

    if not name or not host or not username:
        error = "Name, host, and username are required."
    elif use_ssl_bool and require_starttls_bool:
        error = "Use SSL and Require STARTTLS cannot both be enabled. Choose one."

    if error:
        account_data = {
            "id": None,
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "use_ssl": use_ssl_bool,
            "require_starttls": require_starttls_bool,
            "ca_bundle": ca_bundle,
            "poll_interval_seconds": poll_interval_seconds,
            "delete_after_processing": delete_after_processing == "true",
            "enabled": enabled == "true",
        }
        return templates.TemplateResponse(
            "account_form.html",
            {
                "request": request,
                "account": account_data,
                "error": error,
                "success": None,
            },
        )

    encrypted_pw = encrypt_password(password) if password else ""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO imap_accounts
                (name, host, port, username, password_encrypted,
                 use_ssl, require_starttls, ca_bundle,
                 poll_interval_seconds, delete_after_processing, enabled)
                VALUES
                (:name, :host, :port, :username, :pw,
                 :use_ssl, :require_starttls, :ca_bundle,
                 :poll, :delete_after, :enabled)
                """
            ),
            {
                "name": name.strip(),
                "host": host.strip(),
                "port": port,
                "username": username.strip(),
                "pw": encrypted_pw,
                "use_ssl": use_ssl_bool,
                "require_starttls": require_starttls_bool,
                "ca_bundle": ca_bundle.strip(),
                "poll": poll_interval_seconds,
                "delete_after": delete_after_processing == "true",
                "enabled": enabled == "true",
            },
        )

    return RedirectResponse(url="/accounts", status_code=303)


@app.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_edit_form(request: Request, account_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, name, host, port, username,
                       use_ssl, require_starttls, ca_bundle,
                       poll_interval_seconds, delete_after_processing,
                       enabled
                FROM imap_accounts
                WHERE id = :id
                """
            ),
            {"id": account_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    return templates.TemplateResponse(
        "account_form.html",
        {
            "request": request,
            "account": dict(row),
            "error": None,
            "success": None,
        },
    )


@app.post("/accounts/{account_id}", response_class=HTMLResponse)
def account_edit_submit(
    request: Request,
    account_id: int,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    use_ssl: str = Form("true"),
    require_starttls: str = Form("false"),
    ca_bundle: str = Form(""),
    poll_interval_seconds: int = Form(...),
    delete_after_processing: str = Form("true"),
    enabled: str = Form("true"),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    error = None
    use_ssl_bool = use_ssl == "true"
    require_starttls_bool = require_starttls == "true"

    if not name or not host or not username:
        error = "Name, host, and username are required."
    elif use_ssl_bool and require_starttls_bool:
        error = "Use SSL and Require STARTTLS cannot both be enabled. Choose one."

    if error:
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT id, name, host, port, username,
                           use_ssl, require_starttls, ca_bundle,
                           poll_interval_seconds, delete_after_processing,
                           enabled
                    FROM imap_accounts
                    WHERE id = :id
                    """
                ),
                {"id": account_id},
            ).mappings().first()
        return templates.TemplateResponse(
            "account_form.html",
            {
                "request": request,
                "account": dict(row) if row else None,
                "error": error,
                "success": None,
            },
        )

    fields = {
        "name": name.strip(),
        "host": host.strip(),
        "port": port,
        "username": username.strip(),
        "use_ssl": use_ssl_bool,
        "require_starttls": require_starttls_bool,
        "ca_bundle": ca_bundle.strip(),
        "poll_interval_seconds": poll_interval_seconds,
        "delete_after_processing": delete_after_processing == "true",
        "enabled": enabled == "true",
    }

    set_clauses = [
        "name = :name",
        "host = :host",
        "port = :port",
        "username = :username",
        "use_ssl = :use_ssl",
        "require_starttls = :require_starttls",
        "ca_bundle = :ca_bundle",
        "poll_interval_seconds = :poll",
        "delete_after_processing = :delete_after",
        "enabled = :enabled",
    ]

    params = {
        "id": account_id,
        "name": fields["name"],
        "host": fields["host"],
        "port": fields["port"],
        "username": fields["username"],
        "use_ssl": fields["use_ssl"],
        "require_starttls": fields["require_starttls"],
        "ca_bundle": fields["ca_bundle"],
        "poll": fields["poll_interval_seconds"],
        "delete_after": fields["delete_after_processing"],
        "enabled": fields["enabled"],
    }

    if password:
        set_clauses.append("password_encrypted = :pw")
        params["pw"] = encrypt_password(password)

    sql = f"""
        UPDATE imap_accounts
        SET {", ".join(set_clauses)}
        WHERE id = :id
    """

    with engine.begin() as conn:
        conn.execute(text(sql), params)

    return RedirectResponse(url="/accounts", status_code=303)


@app.post("/accounts/{account_id}/test-imap", response_class=HTMLResponse)
def account_test_imap(request: Request, account_id: int):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    ok, msg = test_imap_account(account_id)

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT id, name, host, port, username,
                       use_ssl, require_starttls, ca_bundle,
                       poll_interval_seconds, delete_after_processing,
                       enabled
                FROM imap_accounts
                WHERE id = :id
                """
            ),
            {"id": account_id},
        ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    return templates.TemplateResponse(
        "account_form.html",
        {
            "request": request,
            "account": dict(row),
            "error": None if ok else msg,
            "success": msg if ok else None,
        },
    )


# ------------------
# Errors view
# ------------------

@app.get("/errors", response_class=HTMLResponse)
def errors_page(
    request: Request,
    source: str | None = Query(default=None),
    limit: int = Query(default=100),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    errors = get_recent_errors(source_prefix=source, limit=limit)
    return templates.TemplateResponse(
        "errors.html",
        {
            "request": request,
            "errors": errors,
            "source": source or "",
            "limit": limit,
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
    accounts = get_accounts()
    msg_count = get_message_count()
    storage_stats = get_storage_stats(settings)
    db_ok, db_msg = test_db()

    return templates.TemplateResponse(
        "status.html",
        {
            "request": request,
            "settings": settings,
            "accounts": accounts,
            "message_count": msg_count,
            "storage_stats": storage_stats,
            "db_ok": db_ok,
            "db_msg": db_msg,
        },
    )


@app.get("/api/status")
def api_status():
    settings = load_settings()
    accounts = get_accounts()
    msg_count = get_message_count()
    storage_stats = get_storage_stats(settings)
    db_ok, db_msg = test_db()

    return {
        "accounts": accounts,
        "message_count": msg_count,
        "storage": storage_stats,
        "db": {"ok": db_ok, "message": db_msg},
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
                SELECT id, account, folder, uid, subject, sender, recipients, date,
                       storage_path, created_at
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

# ------------------
# Administrator Password
# ------------------

@app.get("/admin/password", response_class=HTMLResponse)
def admin_password_form(request: Request):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    return templates.TemplateResponse(
        "admin_password.html",
        {
            "request": request,
            "error": None,
            "success": None,
        },
    )


@app.post("/admin/password", response_class=HTMLResponse)
def admin_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    if not require_login(request):
        return RedirectResponse(url="/login", status_code=303)

    user_id = request.session["user_id"]

    # Validate new password
    if new_password != confirm_password:
        return templates.TemplateResponse(
            "admin_password.html",
            {
                "request": request,
                "error": "New passwords do not match.",
                "success": None,
            },
        )

    with engine.begin() as conn:
        row = conn.execute(
            text(
                """
                SELECT password_hash = crypt(:current, password_hash) AS valid
                FROM users
                WHERE id = :id
                """
            ),
            {"id": user_id, "current": current_password},
        ).mappings().first()

        if not row or not row["valid"]:
            return templates.TemplateResponse(
                "admin_password.html",
                {
                    "request": request,
                    "error": "Current password is incorrect.",
                    "success": None,
                },
            )

        conn.execute(
            text(
                """
                UPDATE users
                SET password_hash = crypt(:new, gen_salt('bf'))
                WHERE id = :id
                """
            ),
            {"id": user_id, "new": new_password},
        )

    return templates.TemplateResponse(
        "admin_password.html",
        {
            "request": request,
            "error": None,
            "success": "Password updated successfully.",
        },
    )
