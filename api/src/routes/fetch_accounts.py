import base64
import traceback
import sys

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query
from utils.security import encrypt_password
from imaplib import IMAP4, IMAP4_SSL

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str):
    request.session["flash"] = message


@router.get("/fetch_accounts")
def list_accounts(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    accounts = query(
        """
        SELECT id, name, account_type, host, port, username, use_ssl, require_starttls,
               poll_interval_seconds, delete_after_processing, enabled,
               last_heartbeat, last_success, last_error
        FROM fetch_accounts
        ORDER BY id
        """
    ).mappings().all()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "fetch_accounts.html",
        {
            "request": request,
            "accounts": accounts,
            "flash": msg,
        },
    )


@router.get("/fetch_accounts/new")
def new_account(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "fetch_account_form.html",
        {
            "request": request,
            "account": None,
            "flash": msg,
        },
    )


@router.post("/fetch_accounts/new")
def create_account(
    request: Request,
    name: str = Form(...),
    account_type: str = Form("imap"),
    host: str = Form(""),
    port: int = Form(993),
    username: str = Form(""),
    password: str = Form(""),
    use_ssl: bool = Form(False),
    require_starttls: bool = Form(False),
    poll_interval_seconds: int = Form(300),
    delete_after_processing: bool = Form(False),
    enabled: bool = Form(True),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    enc = encrypt_password(password) if password else None

    query(
        """
        INSERT INTO fetch_accounts
        (name, account_type, host, port, username, password_encrypted,
         use_ssl, require_starttls, poll_interval_seconds,
         delete_after_processing, enabled)
        VALUES
        (:name, :account_type, :host, :port, :username, :password_encrypted,
         :use_ssl, :require_starttls, :poll_interval_seconds,
         :delete_after_processing, :enabled)
        """,
        {
            "name": name,
            "account_type": account_type,
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

    flash(request, f"{account_type.upper()} account created successfully")
    return RedirectResponse("/fetch_accounts", status_code=303)


@router.get("/fetch_accounts/{id}/edit")
def edit_account(request: Request, id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        """
        SELECT id, name, account_type, host, port, username, password_encrypted,
               use_ssl, require_starttls, poll_interval_seconds,
               delete_after_processing, enabled
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/fetch_accounts", status_code=303)

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "fetch_account_form.html",
        {
            "request": request,
            "account": account,
            "flash": msg,
        },
    )


@router.post("/fetch_accounts/{id}/edit")
def update_account(
    request: Request,
    id: int,
    name: str = Form(...),
    account_type: str = Form("imap"),
    host: str = Form(""),
    port: int = Form(993),
    username: str = Form(""),
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
        UPDATE fetch_accounts
        SET name = :name,
            account_type = :account_type,
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
            "account_type": account_type,
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

    flash(request, f"{account_type.upper()} account updated successfully")
    return RedirectResponse("/fetch_accounts", status_code=303)


@router.post("/fetch_accounts/{id}/delete")
def delete_account(request: Request, id: int, mode: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        "SELECT id, name FROM fetch_accounts WHERE id = :id",
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/fetch_accounts", status_code=303)

    if mode == "retain":
        # Delete account only
        query("DELETE FROM fetch_accounts WHERE id = :id", {"id": id})
        flash(request, f"Fetch account '{account['name']}' deleted. Messages retained.")
        return RedirectResponse("/fetch_accounts", status_code=303)

    elif mode == "delete_messages":
        # Delete messages first
        query(
            "DELETE FROM messages WHERE source = :name",
            {"name": account["name"]},
        )
        # Then delete account
        query("DELETE FROM fetch_accounts WHERE id = :id", {"id": id})

        flash(request, f"Fetch account '{account['name']}' and all related messages deleted.")
        return RedirectResponse("/fetch_accounts", status_code=303)

    flash(request, "Invalid delete mode.")
    return RedirectResponse("/fetch_accounts", status_code=303)


@router.get("/fetch_accounts/{id}/delete/confirm")
def confirm_delete_account(request: Request, id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        """
        SELECT id, name
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/fetch_accounts", status_code=303)

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
        "fetch_account_confirm_delete.html",
        {
            "request": request,
            "account": account,
            "msg_count": msg_count,
        },
    )


@router.post("/fetch_accounts/test")
def test_connection(
    request: Request,
    name: str = Form(""),
    account_type: str = Form("imap"),
    host: str = Form(""),
    port: int = Form(993),
    username: str = Form(""),
    password: str = Form(""),
    use_ssl: bool = Form(False),
    require_starttls: bool = Form(False),
    poll_interval_seconds: int = Form(300),
    delete_after_processing: bool = Form(False),
    enabled: bool = Form(True),
    account_id: int = Form(None),
):
    # ---------------------------------------------------------
    # Load and decrypt stored password if none was provided
    # ---------------------------------------------------------
    if not password and account_id:
        from utils.db import query
        from utils.security import decrypt_password

        acc = query(
            """
            SELECT password_encrypted
            FROM fetch_accounts
            WHERE id = :id
            """,
            {"id": account_id},
        ).mappings().first()

        if acc and acc["password_encrypted"]:
            password = decrypt_password(acc["password_encrypted"])

    try:
        if use_ssl:
            conn = IMAP4_SSL(host, port)
            conn.login(username, password)

        else:
            conn = IMAP4(host, port)

            if require_starttls:
                conn.starttls()

                caps = conn.capability()

                # Normalize capabilities
                normalized_caps = []
                for c in caps:
                    if isinstance(c, list):
                        for sub in c:
                            normalized_caps.append(sub if isinstance(sub, bytes) else str(sub).encode("utf-8"))
                    else:
                        normalized_caps.append(c if isinstance(c, bytes) else str(c).encode("utf-8"))

                caps_flat = b" ".join(normalized_caps)

                if b"AUTH=LOGIN" in caps_flat:
                    conn.login(username, password)

                elif b"AUTH=PLAIN" in caps_flat:
                    def try_plain(authzid, authcid, pw):
                        auth_string = base64.b64encode(
                            f"{authzid}\0{authcid}\0{pw}".encode("utf-8")
                        ).decode("ascii")
                        return conn.authenticate("PLAIN", lambda _: auth_string)

                    try:
                        try_plain("", username, password)
                    except Exception:
                        try:
                            try_plain(username, username, password)
                        except Exception:
                            try:
                                try_plain("", username, password)
                            except Exception:
                                try:
                                    try_plain(username, username, password)
                                except Exception:
                                    raise RuntimeError("SASL PLAIN authentication failed for all variants")

                else:
                    raise RuntimeError("Server does not advertise AUTH=LOGIN or AUTH=PLAIN after STARTTLS")

            else:
                conn.login(username, password)

        conn.logout()
        flash(request, "Connection successful")

    except Exception as e:
        print("=== IMAP TEST ERROR ===", file=sys.stderr)
        traceback.print_exc()
        flash(request, f"Connection failed: {e}")

    account = {
        "id": account_id,
        "name": name,
        "account_type": account_type,
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
        "fetch_account_form.html",
        {"request": request, "account": account, "flash": msg},
    )
