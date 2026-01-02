import base64

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


@router.post("/imap_accounts/{id}/delete")
def delete_account(request: Request, id: int, mode: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        "SELECT id, name FROM imap_accounts WHERE id = :id",
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/imap_accounts", status_code=303)

    if mode == "retain":
        # Delete account only
        query("DELETE FROM imap_accounts WHERE id = :id", {"id": id})
        flash(request, f"IMAP account '{account['name']}' deleted. Messages retained.")
        return RedirectResponse("/imap_accounts", status_code=303)

    elif mode == "delete_messages":
        # Delete messages first
        query(
            "DELETE FROM messages WHERE source = :name",
            {"name": account["name"]},
        )
        # Then delete account
        query("DELETE FROM imap_accounts WHERE id = :id", {"id": id})

        flash(request, f"IMAP account '{account['name']}' and all related messages deleted.")
        return RedirectResponse("/imap_accounts", status_code=303)

    flash(request, "Invalid delete mode.")
    return RedirectResponse("/imap_accounts", status_code=303)


@router.get("/imap_accounts/{id}/delete/confirm")
def confirm_delete_account(request: Request, id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        """
        SELECT id, name
        FROM imap_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/imap_accounts", status_code=303)

    # Count messages linked to this account
    msg_count = query(
        """
        SELECT COUNT(*) AS c
        FROM messages
        WHERE source = :name
        """,
        {"name": account["name"]},
    ).mappings().first()["c"]

    return templates.TemplateResponse(
        "imap_account_confirm_delete.html",
        {
            "request": request,
            "account": account,
            "msg_count": msg_count,
        },
    )


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
            # SSL: straight to IMAP4_SSL, regular LOGIN
            conn = IMAP4_SSL(host, port)
            conn.login(username, password)

        else:
            # Plain IMAP, optional STARTTLS + capability-based auth
            conn = IMAP4(host, port)

            if require_starttls:
                # Upgrade connection
                conn.starttls()

                # Fetch capabilities (may contain bytes, str, or nested lists)
                caps = conn.capability()

                # Normalize capabilities: flatten nested lists and convert to bytes
                normalized_caps = []
                for c in caps:
                    if isinstance(c, list):
                        for sub in c:
                            if isinstance(sub, bytes):
                                normalized_caps.append(sub)
                            else:
                                normalized_caps.append(str(sub).encode("utf-8"))
                    else:
                        if isinstance(c, bytes):
                            normalized_caps.append(c)
                        else:
                            normalized_caps.append(str(c).encode("utf-8"))

                caps_flat = b" ".join(normalized_caps)

                # Choose authentication method based on capabilities
                if b"AUTH=LOGIN" in caps_flat:
                    conn.login(username, password)

                elif b"AUTH=PLAIN" in caps_flat:
                    # SASL PLAIN: base64("\0username\0password")
                    auth_string = base64.b64encode(
                        f"\0{username}\0{password}".encode("utf-8")
                    ).decode("ascii")

                    def auth_plain(_):
                        return auth_string

                    conn.authenticate("PLAIN", auth_plain)

                else:
                    raise RuntimeError(
                        "Server does not advertise AUTH=LOGIN or AUTH=PLAIN after STARTTLS"
                    )

            else:
                # No STARTTLS, plain LOGIN (only safe if server allows it)
                conn.login(username, password)

        conn.logout()
        flash(request, "Connection successful")

    except Exception as e:
        flash(request, f"Connection failed: {e}")

    # Rebuild account dict for re-rendering the form
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

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "imap_account_form.html",
        {"request": request, "account": account, "flash": msg},
    )
