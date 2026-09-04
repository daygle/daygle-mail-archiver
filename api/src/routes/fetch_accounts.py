from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from ..utils.db import query, transaction
from ..utils.crypto import encrypt_password, decrypt_password
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.permissions import require_permission, PERMISSIONS
from ..utils.i18n import request_gettext
from ..utils.timezone import format_datetime, get_display_prefs
from imaplib import IMAP4, IMAP4_SSL

router = APIRouter()

VALID_ACCOUNT_TYPES = {"imap", "gmail", "o365"}

# Fields that are safe to expose to JavaScript (exclude datetime and sensitive data)
JSON_SAFE_FIELDS = [
    'id', 'name', 'account_type', 'host', 'port', 'username',
    'use_ssl', 'require_starttls', 'poll_interval_seconds',
    'delete_after_processing', 'expunge_deleted', 'enabled',
    'oauth_client_id'  # Client ID is public, but NOT client_secret
]


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str, category: str = 'info'):
    request.session["flash"] = {"message": message, "type": category}


def _probe_oauth_api(account_id: int, account_type: str) -> tuple:
    """Run the provider API probe for a saved gmail/o365 account.

    Returns ``(ok, message)``. Raises on transport errors (caught by callers).
    """
    import requests
    from ..utils.oauth_helpers import get_valid_token

    access_token = get_valid_token(account_id, account_type)
    if not access_token:
        return False, f"✗ {account_type.upper()} authentication failed - please re-authorise"

    headers = {"Authorization": f"Bearer {access_token}"}
    if account_type == "gmail":
        response = requests.get(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            email = response.json().get("emailAddress", "unknown")
            return True, f"Gmail API connection successful ({email})"
        return False, f"✗ Gmail API connection failed: {response.status_code}"

    if account_type == "o365":
        response = requests.get(
            "https://graph.microsoft.com/v1.0/me",
            headers=headers,
            timeout=10,
        )
        if response.status_code == 200:
            user = response.json()
            email = user.get("mail") or user.get("userPrincipalName", "unknown")
            return True, f"Office 365 API connection successful ({email})"
        return False, f"✗ Office 365 API connection failed: {response.status_code}"

    return False, f"✗ Unknown account type: {account_type}"


def _test_oauth_connection(request: Request, account_id, account_type: str):
    """Test an OAuth-based (gmail/o365) account connection.

    Used by the new-account form test: a saved account is required before the
    OAuth flow can be exercised, so unsaved accounts get a helpful message.
    """
    _ = request_gettext(request)
    if not account_id:
        flash(
            request,
            _("Save the {0} account first, then use the OAuth Authorise button and test again.")
            .format(account_type.upper()),
            "error",
        )
        return RedirectResponse("/fetch-accounts", status_code=303)

    acc = query(
        """
        SELECT oauth_client_id, oauth_client_secret, oauth_access_token, oauth_refresh_token
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": account_id},
    ).mappings().first()

    if not acc:
        flash(request, _("Account not found"), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    if not acc.get("oauth_client_id") or not acc.get("oauth_client_secret"):
        flash(
            request,
            _("{0} OAuth Client ID and Secret must be configured for this account.")
            .format(account_type.upper()),
            "error",
        )
        return RedirectResponse("/fetch-accounts", status_code=303)

    try:
        ok, msg = _probe_oauth_api(account_id, account_type)
        flash(request, msg, "success" if ok else "error")
    except Exception as e:
        flash(request, _("✗ Connection failed: {0}").format(str(e)), "error")

    return RedirectResponse("/fetch-accounts", status_code=303)


@router.get("/fetch-accounts")
def list_accounts(request: Request, _=require_permission(PERMISSIONS["view_fetch_accounts"]), page: int = 1):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Get page_size from user settings, fallback to global settings
    user_id = request.session.get("user_id")
    page_size = 50  # Default
    
    if user_id:
        user_result = query("SELECT page_size FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user_result and user_result["page_size"]:
            page_size = user_result["page_size"]
    
    if not user_id or not page_size:
        global_result = query("SELECT value FROM settings WHERE key = 'page_size'").mappings().first()
        if global_result:
            page_size = int(global_result["value"])
    
    page_size = min(max(10, page_size), 500)  # Ensure between 10-500
    page = max(1, page)
    offset = (page - 1) * page_size

    # Get total count
    total_result = query("SELECT COUNT(*) as total FROM fetch_accounts").mappings().first()
    total = total_result["total"] if total_result else 0
    total_pages = (total + page_size - 1) // page_size

    # Get paginated accounts with email counts
    accounts_raw = query(
        """
        SELECT fa.id, fa.name, fa.account_type, fa.host, fa.port, fa.username, fa.use_ssl, fa.require_starttls,
               fa.poll_interval_seconds, fa.delete_after_processing, fa.expunge_deleted, fa.enabled,
               fa.oauth_client_id, fa.oauth_client_secret,
               fa.last_heartbeat, fa.last_success, fa.last_error,
               COUNT(e.id) as email_count
        FROM fetch_accounts fa
        LEFT JOIN emails e ON e.source = fa.name
        GROUP BY fa.id, fa.name, fa.account_type, fa.host, fa.port, fa.username, fa.use_ssl, fa.require_starttls,
                 fa.poll_interval_seconds, fa.delete_after_processing, fa.expunge_deleted, fa.enabled,
                 fa.oauth_client_id, fa.oauth_client_secret,
                 fa.last_heartbeat, fa.last_success, fa.last_error
        ORDER BY fa.id
        LIMIT :limit OFFSET :offset
        """,
        {"limit": page_size, "offset": offset}
    ).mappings().all()
    
    # Convert RowMapping objects to dictionaries and create JSON-safe versions
    accounts = []
    for acc in accounts_raw:
        acc_dict = dict(acc)
        # Create a JSON-safe version without datetime fields or sensitive data for JavaScript
        acc_dict['json_safe'] = {
            field: acc_dict[field] for field in JSON_SAFE_FIELDS if field in acc_dict
        }
        accounts.append(acc_dict)

    # Resolve display preferences once and pre-format the worker-health
    # timestamps. The template previously strftime'd the raw UTC values, which
    # ignored the user's timezone entirely; it now renders these fields.
    tz, date_format, time_format = get_display_prefs(user_id)
    for acc in accounts:
        acc["last_heartbeat_formatted"] = (
            format_datetime(acc["last_heartbeat"], user_id, tz=tz, date_format=date_format, time_format=time_format)
            if acc.get("last_heartbeat") else None
        )
        acc["last_success_formatted"] = (
            format_datetime(acc["last_success"], user_id, tz=tz, date_format=date_format, time_format=time_format)
            if acc.get("last_success") else None
        )

    msg = request.session.pop("flash", None)

    from ..utils.config import get_config
    public_base_url = (get_config("PUBLIC_BASE_URL") or "").rstrip("/")

    return templates.TemplateResponse(
        "fetch-accounts.html",
        {
            "request": request,
            "accounts": accounts,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "flash": msg,
            "public_base_url": public_base_url,
        },
    )


@router.get("/fetch-accounts/new")
def new_account(request: Request):
    """Redirect to main page - form is now integrated"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    return RedirectResponse("/fetch-accounts", status_code=303)


@router.post("/fetch-accounts/new")
def create_account(
    request: Request,
    _=require_permission(PERMISSIONS["manage_fetch_accounts"]),
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
    expunge_deleted: bool = Form(False),
    enabled: bool = Form(True),
    oauth_client_id: str = Form(""),
    oauth_client_secret: str = Form(""),
):
    _ = request_gettext(request)
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    if account_type not in VALID_ACCOUNT_TYPES:
        flash(request, _("Invalid account type: {0}").format(account_type), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    enc = encrypt_password(password) if password else None

    try:
        query(
            """
            INSERT INTO fetch_accounts
            (name, account_type, host, port, username, password_encrypted,
             use_ssl, require_starttls, poll_interval_seconds,
             delete_after_processing, expunge_deleted, enabled,
             oauth_client_id, oauth_client_secret)
            VALUES
            (:name, :account_type, :host, :port, :username, :password_encrypted,
             :use_ssl, :require_starttls, :poll_interval_seconds,
             :delete_after_processing, :expunge_deleted, :enabled,
             :oauth_client_id, :oauth_client_secret)
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
                "expunge_deleted": expunge_deleted,
                "enabled": enabled,
                "oauth_client_id": oauth_client_id or None,
                "oauth_client_secret": oauth_client_secret or None,
            },
        )

        username_session = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username_session}' created fetch account '{name}' (type: {account_type})", "")

        flash(request, _("{0} account created successfully").format(account_type.upper()), "success")
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    except Exception as e:
        # Handle duplicate name error
        if "duplicate key" in str(e) or "unique constraint" in str(e).lower():
            flash(request, _("Account name '{0}' already exists. Please choose a different name.").format(name), "error")
        else:
            flash(request, _("Failed to create account: {0}").format(str(e)), "error")

        # Redirect to the list page; the flash message carries the error. The
        # previous re-render omitted the accounts context the template requires.
        return RedirectResponse("/fetch-accounts", status_code=303)


@router.get("/fetch-accounts/{id}/edit")
def edit_account(request: Request, id: int):
    """Redirect to main page - form is now integrated"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    return RedirectResponse("/fetch-accounts", status_code=303)


@router.post("/fetch-accounts/{id}/edit")
def update_account(
    request: Request,
    id: int,
    _=require_permission(PERMISSIONS["manage_fetch_accounts"]),
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
    expunge_deleted: bool = Form(False),
    enabled: bool = Form(True),
    oauth_client_id: str = Form(""),
    oauth_client_secret: str = Form(""),
):
    _ = request_gettext(request)
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    if account_type not in VALID_ACCOUNT_TYPES:
        flash(request, _("Invalid account type: {0}").format(account_type), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    if password.strip():
        enc = encrypt_password(password)
        password_sql = "password_encrypted = :password_encrypted,"
    else:
        enc = None
        password_sql = ""

    if oauth_client_secret.strip():
        oauth_secret_sql = ", oauth_client_secret = :oauth_client_secret"
    else:
        oauth_secret_sql = ""

    try:
        # Check current values to detect no-op updates
        current = query("SELECT name, account_type, host, port, username, use_ssl, require_starttls, poll_interval_seconds, delete_after_processing, expunge_deleted, enabled, oauth_client_id FROM fetch_accounts WHERE id = :id", {"id": id}).mappings().first()
        if current:
            # Normalize values for comparison
            same = (
                (current.get('name') == name) and
                (current.get('account_type') == account_type) and
                (current.get('host') == host) and
                (int(current.get('port') or 0) == int(port or 0)) and
                (current.get('username') == username) and
                (bool(current.get('use_ssl')) == bool(use_ssl)) and
                (bool(current.get('require_starttls')) == bool(require_starttls)) and
                (int(current.get('poll_interval_seconds') or 0) == int(poll_interval_seconds or 0)) and
                (bool(current.get('delete_after_processing')) == bool(delete_after_processing)) and
                (bool(current.get('expunge_deleted')) == bool(expunge_deleted)) and
                (bool(current.get('enabled')) == bool(enabled)) and
                ((current.get('oauth_client_id') or '') == (oauth_client_id or ''))
            )
            if same and not password.strip() and not oauth_client_secret.strip():
                flash(request, _("No changes detected."), 'info')
                return RedirectResponse("/fetch-accounts", status_code=303)
        params = {
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
            "expunge_deleted": expunge_deleted,
            "enabled": enabled,
            "oauth_client_id": oauth_client_id or None,
        }
        if oauth_client_secret.strip():
            params["oauth_client_secret"] = oauth_client_secret

        old_name = current.get('name') if current else None

        with transaction() as conn:
            conn.execute(
                text(
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
                        expunge_deleted = :expunge_deleted,
                        enabled = :enabled,
                        oauth_client_id = :oauth_client_id{oauth_secret_sql}
                    WHERE id = :id
                    """
                ),
                params,
            )

            # Emails and quarantined emails reference the account by name; keep
            # them pointing at the new name so history is not orphaned on rename.
            if old_name and old_name != name:
                conn.execute(
                    text("UPDATE emails SET source = :new_name WHERE source = :old_name"),
                    {"new_name": name, "old_name": old_name},
                )
                conn.execute(
                    text(
                        "UPDATE quarantined_emails SET original_source = :new_name "
                        "WHERE original_source = :old_name"
                    ),
                    {"new_name": name, "old_name": old_name},
                )

        username_session = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username_session}' updated fetch account '{name}' (ID: {id})", "")

        flash(request, _("{0} account updated successfully").format(account_type.upper()), "success")
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    except Exception as e:
        # Handle duplicate name error
        if "duplicate key" in str(e) or "unique constraint" in str(e).lower():
            flash(request, _("Account name '{0}' already exists. Please choose a different name.").format(name), "error")
        else:
            flash(request, _("Failed to update account: {0}").format(str(e)), "error")
        
        # Redirect back to edit form
        return RedirectResponse("/fetch-accounts", status_code=303)


@router.post("/fetch-accounts/{id}/delete")
def delete_account(request: Request, id: int, _=require_permission(PERMISSIONS["manage_fetch_accounts"]), mode: str = Form(...)):
    _ = request_gettext(request)
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        "SELECT id, name FROM fetch_accounts WHERE id = :id",
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, _("Account not found"), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    if mode == "retain":
        # Delete account only
        query("DELETE FROM fetch_accounts WHERE id = :id", {"id": id})
        username = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username}' deleted fetch account '{account['name']}' (ID: {id}), emails retained", "")
        flash(request, _("Fetch account '{0}' deleted. Emails retained.").format(account['name']), "success")
        return RedirectResponse("/fetch-accounts", status_code=303)

    elif mode == "delete_messages":
        # Delete emails, quarantined emails and the account atomically
        with transaction() as conn:
            conn.execute(text("DELETE FROM emails WHERE source = :name"), {"name": account["name"]})
            conn.execute(
                text("DELETE FROM quarantined_emails WHERE original_source = :name"),
                {"name": account["name"]},
            )
            conn.execute(text("DELETE FROM fetch_accounts WHERE id = :id"), {"id": id})

        username = request.session.get("username", "unknown")
        log("warning", "Fetch Accounts", f"User '{username}' deleted fetch account '{account['name']}' (ID: {id}) and all related emails", "")

        flash(request, _("Fetch account '{0}' and all related emails deleted.").format(account['name']), "success")
        return RedirectResponse("/fetch-accounts", status_code=303)

    flash(request, _("Invalid delete mode."), "error")
    return RedirectResponse("/fetch-accounts", status_code=303)


@router.get("/fetch-accounts/{id}/test")
def test_account_connection(
    request: Request,
    id: int,
    _=require_permission(PERMISSIONS["manage_fetch_accounts"]),
):
    """Test connection for an existing fetch account"""
    _ = request_gettext(request)
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Load account details
    acc = query(
        """
        SELECT id, name, account_type, host, port, username, password_encrypted, 
               use_ssl, require_starttls, oauth_access_token, oauth_refresh_token,
               oauth_client_id, oauth_client_secret
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not acc:
        flash(request, _("Account not found"), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    account_type = acc["account_type"]
    
    try:
        if account_type == "imap":
            # Test IMAP connection
            password = decrypt_password(acc["password_encrypted"]) if acc["password_encrypted"] else ""
            
            conn = None
            try:
                if acc["use_ssl"]:
                    conn = IMAP4_SSL(acc["host"], acc["port"])
                    conn.login(acc["username"], password)
                else:
                    conn = IMAP4(acc["host"], acc["port"])
                    if acc["require_starttls"]:
                        conn.starttls()
                    conn.login(acc["username"], password)
                
                flash(request, _("IMAP connection successful to {0}").format(acc['host']), "success")
            finally:
                if conn:
                    try:
                        conn.logout()
                    except Exception:
                        pass
            
        elif account_type in ("gmail", "o365"):
            # Test OAuth API connection
            if not acc["oauth_client_id"] or not acc["oauth_client_secret"]:
                flash(
                    request,
                    _("{0} OAuth Client ID and Secret must be configured for this account")
                    .format(account_type.upper()),
                    "error",
                )
                return RedirectResponse("/fetch-accounts", status_code=303)

            ok, msg = _probe_oauth_api(id, account_type)
            flash(request, msg, "success" if ok else "error")
        else:
            flash(request, _("✗ Unknown account type: {0}").format(account_type), "error")
            
    except Exception as e:
        flash(request, _("✗ Connection failed: {0}").format(str(e)), "error")

    return RedirectResponse("/fetch-accounts", status_code=303)


@router.post("/fetch-accounts/test")
def test_connection(
    request: Request,
    _=require_permission(PERMISSIONS["manage_fetch_accounts"]),
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
    expunge_deleted: bool = Form(False),
    enabled: bool = Form(True),
    account_id: int = Form(None),
):
    # ---------------------------------------------------------
    # Load and decrypt stored password if none was provided
    # ---------------------------------------------------------
    _ = request_gettext(request)
    if account_type not in VALID_ACCOUNT_TYPES:
        flash(request, _("Invalid account type: {0}").format(account_type), "error")
        return RedirectResponse("/fetch-accounts", status_code=303)

    if account_type != "imap":
        return _test_oauth_connection(request, account_id, account_type)

    if not password and account_id:
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

    conn = None
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
                    import base64
                    
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
                            raise RuntimeError("SASL PLAIN authentication failed for all variants") from None

                else:
                    raise RuntimeError("Server does not advertise AUTH=LOGIN or AUTH=PLAIN after STARTTLS")

            else:
                conn.login(username, password)

        flash(request, _("Connection successful"), "success")

    except Exception as e:
        log("error", "FetchAccounts", f"IMAP test connection failed: {str(e)}")
        flash(request, _("Connection failed: {0}").format(str(e)), "error")
    finally:
        if conn:
            try:
                conn.logout()
            except Exception:
                pass

    # The result is flashed; return to the list page like every other branch
    # of this route. The previous re-render omitted the accounts context the
    # template requires and would have crashed.
    return RedirectResponse("/fetch-accounts", status_code=303)
