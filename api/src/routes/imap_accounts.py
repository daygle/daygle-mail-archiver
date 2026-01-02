from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query
from utils.security import encrypt_password

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return request.session.get("user") is not None

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

    return templates.TemplateResponse(
        "imap_accounts.html",
        {"request": request, "accounts": accounts},
    )

@router.get("/imap_accounts/new")
def new_account(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "imap_account_form.html",
        {"request": request, "account": None},
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

    return RedirectResponse("/imap_accounts", status_code=303)