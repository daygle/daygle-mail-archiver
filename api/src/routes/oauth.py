"""OAuth2 routes for Gmail and Office 365 integration"""
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
import urllib.parse
import requests
from datetime import datetime, timezone, timedelta

from utils.db import query, execute

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


@router.get("/oauth/gmail/start/{account_id}")
def gmail_oauth_start(request: Request, account_id: int):
    """Initiate Gmail OAuth flow"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_client_id"]:
        request.session["flash"] = "OAuth Client ID not configured for this account"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    # Build OAuth URL
    redirect_uri = request.url_for("gmail_oauth_callback", account_id=account_id)
    params = {
        "client_id": account["oauth_client_id"],
        "redirect_uri": str(redirect_uri),
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline",
        "prompt": "consent"
    }
    
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/oauth/gmail/callback/{account_id}")
def gmail_oauth_callback(request: Request, account_id: int, code: str = None, error: str = None):
    """Handle Gmail OAuth callback"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if error:
        request.session["flash"] = f"OAuth error: {error}"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    if not code:
        request.session["flash"] = "No authorization code received"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account:
        request.session["flash"] = "Account not found"
        return RedirectResponse("/fetch_accounts", status_code=303)
    
    # Exchange code for tokens
    token_url = "https://oauth2.googleapis.com/token"
    redirect_uri = request.url_for("gmail_oauth_callback", account_id=account_id)
    data = {
        "code": code,
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "redirect_uri": str(redirect_uri),
        "grant_type": "authorization_code"
    }
    
    try:
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        token_data = response.json()
        
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
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
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry": expiry,
                "id": account_id
            }
        )
        
        request.session["flash"] = "Gmail OAuth authorization successful!"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
        
    except Exception as e:
        request.session["flash"] = f"OAuth token exchange failed: {str(e)}"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)


@router.get("/oauth/o365/start/{account_id}")
def o365_oauth_start(request: Request, account_id: int):
    """Initiate Office 365 OAuth flow"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_client_id"]:
        request.session["flash"] = "OAuth Client ID not configured for this account"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    # Build OAuth URL
    redirect_uri = request.url_for("o365_oauth_callback", account_id=account_id)
    params = {
        "client_id": account["oauth_client_id"],
        "redirect_uri": str(redirect_uri),
        "response_type": "code",
        "scope": "https://graph.microsoft.com/Mail.Read offline_access",
        "response_mode": "query"
    }
    
    auth_url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    return RedirectResponse(auth_url)


@router.get("/oauth/o365/callback/{account_id}")
def o365_oauth_callback(request: Request, account_id: int, code: str = None, error: str = None):
    """Handle Office 365 OAuth callback"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    if error:
        request.session["flash"] = f"OAuth error: {error}"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    if not code:
        request.session["flash"] = "No authorization code received"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
    
    # Get account details
    account = query(
        "SELECT oauth_client_id, oauth_client_secret FROM fetch_accounts WHERE id = :id",
        {"id": account_id}
    ).mappings().first()
    
    if not account:
        request.session["flash"] = "Account not found"
        return RedirectResponse("/fetch_accounts", status_code=303)
    
    # Exchange code for tokens
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    redirect_uri = request.url_for("o365_oauth_callback", account_id=account_id)
    data = {
        "code": code,
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "redirect_uri": str(redirect_uri),
        "grant_type": "authorization_code",
        "scope": "https://graph.microsoft.com/Mail.Read offline_access"
    }
    
    try:
        response = requests.post(token_url, data=data)
        response.raise_for_status()
        token_data = response.json()
        
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
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
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry": expiry,
                "id": account_id
            }
        )
        
        request.session["flash"] = "Office 365 OAuth authorization successful!"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
        
    except Exception as e:
        request.session["flash"] = f"OAuth token exchange failed: {str(e)}"
        return RedirectResponse(f"/fetch_accounts/{account_id}/edit", status_code=303)
