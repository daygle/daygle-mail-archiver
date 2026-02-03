"""OAuth2 helper functions for Gmail and Office 365 integration"""
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from .db import query, execute
from .crypto import encrypt_password, decrypt_password


def refresh_gmail_token(account_id: int) -> Optional[str]:
    """Refresh Gmail access token using refresh token"""
    account = query(
        """
        SELECT oauth_client_id, oauth_client_secret, oauth_refresh_token
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_refresh_token"]:
        return None
    
    # Decrypt the refresh token before using it
    try:
        refresh_token = decrypt_password(account["oauth_refresh_token"])
    except Exception:
        return None
    
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    
    response = requests.post(token_url, data=data, timeout=30)
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        # Encrypt the access token before storing
        encrypted_access = encrypt_password(access_token)
        
        # Update database
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :token, oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {"token": encrypted_access, "expiry": expiry, "id": account_id}
        )
        
        return access_token
    
    return None


def refresh_o365_token(account_id: int) -> Optional[str]:
    """Refresh Office 365 access token using refresh token"""
    account = query(
        """
        SELECT oauth_client_id, oauth_client_secret, oauth_refresh_token
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": account_id}
    ).mappings().first()
    
    if not account or not account["oauth_refresh_token"]:
        return None
    
    # Decrypt the refresh token before using it
    try:
        refresh_token = decrypt_password(account["oauth_refresh_token"])
    except Exception:
        return None
    
    token_url = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
    data = {
        "client_id": account["oauth_client_id"],
        "client_secret": account["oauth_client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
        "scope": "https://graph.microsoft.com/Mail.Read offline_access"
    }
    
    response = requests.post(token_url, data=data, timeout=30)
    if response.status_code == 200:
        token_data = response.json()
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        
        # Encrypt the access token before storing
        encrypted_access = encrypt_password(access_token)
        
        # Update database
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :token, oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {"token": encrypted_access, "expiry": expiry, "id": account_id}
        )
        
        return access_token
    
    return None


def get_valid_token(account_id: int, account_type: str) -> Optional[str]:
    """Get a valid access token, refreshing if necessary"""
    account = query(
        """
        SELECT oauth_access_token, oauth_token_expiry
        FROM fetch_accounts
        WHERE id = :id
        """,
        {"id": account_id}
    ).mappings().first()
    
    if not account:
        return None
    
    # Check if token is still valid (with 5 minute buffer)
    if account["oauth_access_token"] and account["oauth_token_expiry"]:
        if account["oauth_token_expiry"] > datetime.now(timezone.utc) + timedelta(minutes=5):
            # Decrypt the access token before returning
            try:
                return decrypt_password(account["oauth_access_token"])
            except Exception:
                pass  # Token decryption failed, try refreshing
    
    # Token expired or missing, refresh it
    if account_type == "gmail":
        return refresh_gmail_token(account_id)
    elif account_type == "o365":
        return refresh_o365_token(account_id)
    
    return None
