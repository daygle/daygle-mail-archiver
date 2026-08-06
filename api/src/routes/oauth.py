"""OAuth2 routes for Gmail and Office 365 integration"""
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
import urllib.parse
import requests
from datetime import datetime, timezone, timedelta

from ..utils.db import query, execute
from ..utils.logger import log
from ..utils.crypto import encrypt_password
from ..utils.config import get_config
from ..utils.templates import templates

router = APIRouter()

# Scopes must cover the operations the worker performs. gmail.readonly / Mail.Read
# only permit reading; "delete after processing" moves messages to Trash (Gmail)
# or deletes them (Graph), which require write scopes.
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
O365_SCOPE = "https://graph.microsoft.com/Mail.ReadWrite offline_access"


def require_login(request: Request):
    return "user_id" in request.session


def _build_redirect_uri(request: Request, provider: str, account_id: int) -> str:
    """Build the OAuth redirect URI for the callback.

    Uses PUBLIC_BASE_URL when configured so the URI matches what is registered
    in Google/Azure even when the API runs behind a reverse proxy (otherwise
    request.url_for reflects the internal scheme/host and the callback URI
    mismatch causes OAuth to fail).
    """
    public_base = (get_config("PUBLIC_BASE_URL") or "").rstrip("/")
    if public_base:
        return f"{public_base}/oauth/{provider}/callback/{account_id}"
    return str(request.url_for(f"{provider}_oauth_callback", account_id=account_id))


def _new_oauth_state(request: Request, provider: str, account_id: int) -> str:
    """Generate a CSRF state token and stash it in the session."""
    state = secrets.token_urlsafe(32)
    request.session[f"oauth_state_{provider}_{account_id}"] = state
    return state


def _verify_oauth_state(request: Request, provider: str, account_id: int, state) -> bool:
    """Verify the state echoed back by the provider matches the one we sent."""
    expected = request.session.pop(f"oauth_state_{provider}_{account_id}", None)
    return bool(expected and state and secrets.compare_digest(expected, state))


@router.get("/oauth/gmail/start/{account_id}")
def gmail_oauth_start(request: Request, account_id: int):
    """Initiate Gmail OAuth flow"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_client_id"] or not account["oauth_client_secret"]:
        request.session["flash"] = "OAuth Client ID and Secret must be configured for this account"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    username = request.session.get("username", "unknown")
    log("info", "OAuth", f"User '{username}' initiated Gmail OAuth for account {account_id}", "")
    
    # Build OAuth URL
    redirect_uri = _build_redirect_uri(request, "gmail", account_id)
    state = _new_oauth_state(request, "gmail", account_id)
    params = {
        "client_id": account["oauth_client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/oauth/gmail/callback/{account_id}")
def gmail_oauth_callback(request: Request, account_id: int, code: str = None, error: str = None, state: str = None):
    """Handle Gmail OAuth callback"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Verify state first (and always clear it) so forged callbacks and provider
    # error responses cannot bypass CSRF protection.
    if not _verify_oauth_state(request, "gmail", account_id, state):
        log("warning", "OAuth", f"OAuth state mismatch for Gmail account {account_id}")
        request.session["flash"] = "OAuth authorisation failed: state verification failed. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)

    if error:
        request.session["flash"] = f"OAuth error: {error}"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    if not code:
        request.session["flash"] = "No authorisation code received"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account:
        request.session["flash"] = "Account not found"
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    # Exchange code for tokens
    token_url = "https://oauth2.googleapis.com/token"
    redirect_uri = _build_redirect_uri(request, "gmail", account_id)
    data = {
        "code": code,
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "redirect_uri": str(redirect_uri),
        "grant_type": "authorization_code"
    }
    
    try:
        response = requests.post(token_url, data=data, timeout=30)
        response.raise_for_status()
        token_data = response.json()
        
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        
        if not access_token:
            raise ValueError("No access token received from Google")
        
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        # Encrypt tokens before storing
        encrypted_access = encrypt_password(access_token)
        encrypted_refresh = encrypt_password(refresh_token) if refresh_token else None
        
        # Store tokens
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :access_token,
                oauth_refresh_token = :refresh_token,
                oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {
                "access_token": encrypted_access,
                "refresh_token": encrypted_refresh,
                "expiry": expiry,
                "id": account_id
            }
        )
        
        username = request.session.get("username", "unknown")
        log("info", "OAuth", f"User '{username}' successfully completed Gmail OAuth for account {account_id}", "")
        
        request.session["flash"] = "Gmail OAuth authorisation successful!"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
        
    except requests.exceptions.Timeout:
        log("error", "OAuth", f"Gmail OAuth timeout for account {account_id}", "")
        request.session["flash"] = "OAuth request timed out. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    except requests.exceptions.RequestException as e:
        log("error", "OAuth", f"Gmail OAuth request failed for account {account_id}: {str(e)}", "")
        request.session["flash"] = "OAuth request failed. Please check your credentials and try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    except Exception as e:
        log("error", "OAuth", f"Gmail OAuth error for account {account_id}: {str(e)}", "")
        request.session["flash"] = "OAuth authorisation failed. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)


@router.get("/oauth/o365/start/{account_id}")
def o365_oauth_start(request: Request, account_id: int):
    """Initiate Office 365 OAuth flow"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_client_id"] or not account["oauth_client_secret"]:
        request.session["flash"] = "OAuth Client ID and Secret must be configured for this account"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    username = request.session.get("username", "unknown")
    log("info", "OAuth", f"User '{username}' initiated Office 365 OAuth for account {account_id}", "")
    
    # Build OAuth URL
    redirect_uri = _build_redirect_uri(request, "o365", account_id)
    state = _new_oauth_state(request, "o365", account_id)
    params = {
        "client_id": account["oauth_client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": O365_SCOPE,
        "response_mode": "query",
        "state": state,
    }
    
    auth_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/oauth/o365/callback/{account_id}")
def o365_oauth_callback(request: Request, account_id: int, code: str = None, error: str = None, state: str = None):
    """Handle Office 365 OAuth callback"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Verify state first (and always clear it) so forged callbacks and provider
    # error responses cannot bypass CSRF protection.
    if not _verify_oauth_state(request, "o365", account_id, state):
        log("warning", "OAuth", f"OAuth state mismatch for Office 365 account {account_id}")
        request.session["flash"] = "OAuth authorisation failed: state verification failed. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)

    if error:
        request.session["flash"] = f"OAuth error: {error}"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    if not code:
        request.session["flash"] = "No authorisation code received"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account:
        request.session["flash"] = "Account not found"
        return RedirectResponse("/fetch-accounts", status_code=303)
    
    # Exchange code for tokens
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    redirect_uri = _build_redirect_uri(request, "o365", account_id)
    data = {
        "code": code,
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "redirect_uri": str(redirect_uri),
        "grant_type": "authorization_code",
        "scope": "https://graph.microsoft.com/Mail.Read offline_access"
    }
    
    try:
        response = requests.post(token_url, data=data, timeout=30)
        response.raise_for_status()
        token_data = response.json()
        
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        
        if not access_token:
            raise ValueError("No access token received from Microsoft")
        
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        # Encrypt tokens before storing
        encrypted_access = encrypt_password(access_token)
        encrypted_refresh = encrypt_password(refresh_token) if refresh_token else None
        
        # Store tokens
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :access_token,
                oauth_refresh_token = :refresh_token,
                oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {
                "access_token": encrypted_access,
                "refresh_token": encrypted_refresh,
                "expiry": expiry,
                "id": account_id
            }
        )
        
        username = request.session.get("username", "unknown")
        log("info", "OAuth", f"User '{username}' successfully completed Office 365 OAuth for account {account_id}", "")
        
        request.session["flash"] = "Office 365 OAuth authorisation successful!"
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
        
    except requests.exceptions.Timeout:
        log("error", "OAuth", f"Office 365 OAuth timeout for account {account_id}", "")
        request.session["flash"] = "OAuth request timed out. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    except requests.exceptions.RequestException as e:
        log("error", "OAuth", f"Office 365 OAuth request failed for account {account_id}: {str(e)}", "")
        request.session["flash"] = "OAuth request failed. Please check your credentials and try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
    except Exception as e:
        log("error", "OAuth", f"Office 365 OAuth error for account {account_id}: {str(e)}", "")
        request.session["flash"] = "OAuth authorisation failed. Please try again."
        return RedirectResponse(f"/fetch-accounts/{account_id}/edit", status_code=303)
