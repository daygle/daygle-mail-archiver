from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query
from utils.security import encrypt_password
from imaplib import IMAP4, IMAP4_SSL

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return request.session.get("user") is not None


def flash(request: Request, message: str):
    request.session["flash"] = message


@router.get("/imap_accounts")
def list_accounts(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    accounts = query(
        """
        SELECT id, name, host, port, username, use_ssl, require_starttls,
               poll_interval_seconds, delete_after_processing, enabled,
               last_heartbeat, last_success, last_error
        FROM imap_accounts
        ORDER BY id
        """
    ).mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "imap_accounts.html",
        {
            "request": request,
            "accounts": accounts,
            "flash": msg,
        },
    )


@router.get("/imap_accounts/new")
def new_account(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "imap_account_form.html",
        {
            "request": request,
            "account": None,
            "flash": msg,
        },
    )


@router.post("/imap_accounts/new")
def create_account(
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
        return RedirectResponse("/login", status_code=303)

    enc = encrypt_password(password)

    query(
        """
        INSERT INTO imap_accounts
        (name, host, port, username, password_encrypted,
         use_ssl, require_starttls, poll_interval_seconds,
         delete_after_processing, enabled)
        VALUES
        (:name, :host, :port, :username, :password_encrypted,
         :use_ssl, :require_starttls, :poll_interval_seconds,
         :delete_after_processing, :enabled)
        """,
        {
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "password_encrypted": enc,
            "use_ssl": use_ssl,
            "require_starttls": require_starttls,
            "poll_interval_seconds": poll_interval_seconds,
            "delete_after_processing": delete_after_processing,
            "enabled": enabled,
        },
    )

    flash(request, "IMAP account created successfully")
    return RedirectResponse("/imap_accounts", status_code=303)


@router.get("/imap_accounts/{id}/edit")
def edit_account(request: Request, id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        """
        SELECT id, name, host, port, username, password_encrypted,
               use_ssl, require_starttls, poll_interval_seconds,
               delete_after_processing, enabled
        FROM imap_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/imap_accounts", status_code=303)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "imap_account_form.html",
        {
            "request": request,
            "account": account,
            "flash": msg,
        },
    )


@router.post("/imap_accounts/{id}/edit")
def update_account(
    request: Request,
    id: int,
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
        return RedirectResponse("/login", status_code=303)

    if password.strip():
        enc = encrypt_password(password)
        password_sql = "password_encrypted = :password_encrypted,"
    else:
        enc = None
        password_sql = ""

    query(
        f"""
        UPDATE imap_accounts
        SET name = :name,
            host = :host,
            port = :port,
            username = :username,
            {password_sql}
            use_ssl = :use_ssl,
            require_starttls = :require_starttls,
            poll_interval_seconds = :poll_interval_seconds,
            delete_after_processing = :delete_after_processing,
            enabled = :enabled
        WHERE id = :id
        """,
        {
            "id": id,
            "name": name,
            "host": host,
            "port": port,
            "username": username,
            "password_encrypted": enc,
            "use_ssl": use_ssl,
            "require_starttls": require_starttls,
            "poll_interval_seconds": poll_interval_seconds,
            "delete_after_processing": delete_after_processing,
            "enabled": enabled,
        },
    )

    flash(request, "IMAP account updated successfully")
    return RedirectResponse("/imap_accounts", status_code=303)


@router.post("/imap_accounts/test")
def test_connection(
    request: Request,
    name: str = Form(""),
    host: str = Form(...),
    port: int = Form(...),
    username: str = Form(...),
    password: str = Form(""),
    use_ssl: bool = Form(False),
    require_starttls: bool = Form(False),
    poll_interval_seconds: int = Form(300),
    delete_after_processing: bool = Form(False),
    enabled: bool = Form(True),
    account_id: int = Form(None),
):
    try:
        if use_ssl:
            conn = IMAP4_SSL(host, port)
        else:
            conn = IMAP4(host, port)
            if require_starttls:
                conn.starttls()

        conn.login(username, password)
        conn.logout()

        flash(request, "Connection successful")
    except Exception as e:
        flash(request, f"Connection failed: {e}")

    account = {
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

    return templates.TemplateResponse(
        "imap_account_form.html",
        {
            "request": request,
            "account": account,
            "flash": request.session.pop("flash", None),
        },
    )
