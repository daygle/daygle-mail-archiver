import traceback
import sys

from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse

from utils.db import query
from utils.security import encrypt_password, decrypt_password
from utils.logger import log
from utils.templates import templates
from imaplib import IMAP4, IMAP4_SSL

router = APIRouter()

# Fields that are safe to expose to JavaScript (exclude datetime and sensitive data)
JSON_SAFE_FIELDS = [
    'id', 'name', 'account_type', 'host', 'port', 'username',
    'use_ssl', 'require_starttls', 'poll_interval_seconds',
    'delete_after_processing', 'expunge_deleted', 'enabled',
    'oauth_client_id'  # Client ID is public, but NOT client_secret
]


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str):
    request.session["flash"] = message


@router.get("/fetch-accounts")
def list_accounts(request: Request, page: int = 1):
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

    msg = request.session.pop("flash", None)

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
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    enc = encrypt_password(password) if password else None

    try:
        query(
            """
            INSERT INTO fetch_accounts
            (name, account_type, host, port, username, password_encrypted,
             use_ssl, require_starttls, poll_interval_seconds,
             delete_after_processing, expunge_deleted, enabled)
            VALUES
            (:name, :account_type, :host, :port, :username, :password_encrypted,
             :use_ssl, :require_starttls, :poll_interval_seconds,
             :delete_after_processing, :expunge_deleted, :enabled)
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
            },
        )

        username_session = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username_session}' created fetch account '{name}' (type: {account_type})", "")

        flash(request, f"{account_type.upper()} account created successfully")
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    except Exception as e:
        # Handle duplicate name error
        if "duplicate key" in str(e) or "unique constraint" in str(e).lower():
            flash(request, f"Account name '{name}' already exists. Please choose a different name.")
        else:
            flash(request, f"Failed to create account: {str(e)}")
        
        # Return to form with data
        account = {
            "name": name,
            "account_type": account_type,
            "host": host,
            "port": port,
            "username": username,
            "use_ssl": use_ssl,
            "require_starttls": require_starttls,
            "poll_interval_seconds": poll_interval_seconds,
            "delete_after_processing": delete_after_processing,
            "expunge_deleted": expunge_deleted,
            "enabled": enabled,
        }
        
        msg = request.session.pop("flash", None)
        return templates.TemplateResponse(
            "fetch-accounts.html",
            {"request": request, "account": account, "flash": msg}
        )


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
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    if password.strip():
        enc = encrypt_password(password)
        password_sql = "password_encrypted = :password_encrypted,"
    else:
        enc = None
        password_sql = ""

    try:
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
                expunge_deleted = :expunge_deleted,
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
                "expunge_deleted": expunge_deleted,
                "enabled": enabled,
            },
        )

        username_session = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username_session}' updated fetch account '{name}' (ID: {id})", "")

        flash(request, f"{account_type.upper()} account updated successfully")
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    except Exception as e:
        # Handle duplicate name error
        if "duplicate key" in str(e) or "unique constraint" in str(e).lower():
            flash(request, f"Account name '{name}' already exists. Please choose a different name.")
        else:
            flash(request, f"Failed to update account: {str(e)}")
        
        # Redirect back to edit form
        return RedirectResponse(f"/fetch-accounts/{id}/edit", status_code=303)


@router.post("/fetch-accounts/{id}/delete")
def delete_account(request: Request, id: int, mode: str = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    account = query(
        "SELECT id, name FROM fetch_accounts WHERE id = :id",
        {"id": id},
    ).mappings().first()

    if not account:
        flash(request, "Account not found")
        return RedirectResponse("/fetch-accounts", status_code=303)

    if mode == "retain":
        # Delete account only
        query("DELETE FROM fetch_accounts WHERE id = :id", {"id": id})
        username = request.session.get("username", "unknown")
        log("info", "Fetch Accounts", f"User '{username}' deleted fetch account '{account['name']}' (ID: {id}), emails retained", "")
        flash(request, f"Fetch account '{account['name']}' deleted. Emails retained.")
        return RedirectResponse("/fetch-accounts", status_code=303)

    elif mode == "delete_messages":
        # Delete emails first
        query(
            "DELETE FROM emails WHERE source = :name",
            {"name": account["name"]},
        )
        # Then delete account
        query("DELETE FROM fetch_accounts WHERE id = :id", {"id": id})

        username = request.session.get("username", "unknown")
        log("warning", "Fetch Accounts", f"User '{username}' deleted fetch account '{account['name']}' (ID: {id}) and all related emails", "")

        flash(request, f"Fetch account '{account['name']}' and all related emails deleted.")
        return RedirectResponse("/fetch-accounts", status_code=303)

    flash(request, "Invalid delete mode.")
    return RedirectResponse("/fetch-accounts", status_code=303)


@router.get("/fetch-accounts/{id}/test")
def test_account_connection(request: Request, id: int):
    """Test connection for an existing fetch account"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Load account details
    acc = query(
        """
        SELECT id, name, account_type, host, port, username, password_encrypted, 
               use_ssl, require_starttls, oauth_access_token, oauth_refresh_token
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": id},
    ).mappings().first()

    if not acc:
        flash(request, "Account not found")
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
                
                flash(request, f"✓ IMAP connection successful to {acc['host']}")
            finally:
                if conn:
                    try:
                        conn.logout()
                    except:
                        pass
            
        elif account_type == "gmail":
            # Test Gmail API connection
            import requests
            from utils.oauth_helpers import get_valid_token
            
            access_token = get_valid_token(id, "gmail")
            if not access_token:
                flash(request, "✗ Gmail authentication failed - please re-authorize")
            else:
                # Test API call
                headers = {"Authorization": f"Bearer {access_token}"}
                response = requests.get(
                    "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    email = response.json().get("emailAddress", "unknown")
                    flash(request, f"✓ Gmail API connection successful ({email})")
                else:
                    flash(request, f"✗ Gmail API connection failed: {response.status_code}")
                    
        elif account_type == "o365":
            # Test Office 365 Graph API connection
            import requests
            from utils.oauth_helpers import get_valid_token
            
            access_token = get_valid_token(id, "o365")
            if not access_token:
                flash(request, "✗ Office 365 authentication failed - please re-authorize")
            else:
                # Test API call
                headers = {"Authorization": f"Bearer {access_token}"}
                response = requests.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers=headers,
                    timeout=10
                )
                if response.status_code == 200:
                    user = response.json()
                    email = user.get("mail") or user.get("userPrincipalName", "unknown")
                    flash(request, f"✓ Office 365 API connection successful ({email})")
                else:
                    flash(request, f"✗ Office 365 API connection failed: {response.status_code}")
        else:
            flash(request, f"✗ Unknown account type: {account_type}")
            
    except Exception as e:
        flash(request, f"✗ Connection failed: {str(e)}")

    return RedirectResponse("/fetch-accounts", status_code=303)


@router.post("/fetch-accounts/test")
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
    expunge_deleted: bool = Form(False),
    enabled: bool = Form(True),
    account_id: int = Form(None),
):
    # ---------------------------------------------------------
    # Load and decrypt stored password if none was provided
    # ---------------------------------------------------------
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
                            raise RuntimeError("SASL PLAIN authentication failed for all variants")

                else:
                    raise RuntimeError("Server does not advertise AUTH=LOGIN or AUTH=PLAIN after STARTTLS")

            else:
                conn.login(username, password)

        flash(request, "Connection successful")

    except Exception as e:
        print("=== IMAP TEST ERROR ===", file=sys.stderr)
        traceback.print_exc()
        flash(request, f"Connection failed: {str(e)}")
    finally:
        if conn:
            try:
                conn.logout()
            except:
                pass

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
        "expunge_deleted": expunge_deleted,
        "enabled": enabled,
    }

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "fetch-accounts.html",
        {"request": request, "account": account, "flash": msg},
    )
