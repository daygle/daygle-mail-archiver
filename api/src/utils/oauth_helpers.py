"""OAuth2 helper functions for Gmail and Office 365 integration"""
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from .db import query, execute
from .crypto import encrypt_password, decrypt_password

logger = logging.getLogger(__name__)

# Keep in sync with the scopes requested in api/src/routes/oauth.py. Write
# scopes are required because the worker deletes/moves processed messages.
O365_REFRESH_SCOPE = "https://graph.microsoft.com/Mail.ReadWrite offline_access"


def _store_tokens(account_id: int, access_token: str, expires_in: int, new_refresh_token: Optional[str] = None):
    """Persist a refreshed access token (and rotated refresh token, if any)."""
    encrypted_access = encrypt_password(access_token)
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

    if new_refresh_token:
        # Providers may rotate refresh tokens (Microsoft does); the old one
        # can be revoked after rotation, so store the replacement.
        encrypted_refresh = encrypt_password(new_refresh_token)
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :token, oauth_token_expiry = :expiry,
                oauth_refresh_token = :refresh_token
            WHERE id = :id
            """,
            {
                "token": encrypted_access,
                "expiry": expiry,
                "refresh_token": encrypted_refresh,
                "id": account_id,
            },
        )
    else:
        execute(
            """
            UPDATE fetch_accounts
            SET oauth_access_token = :token, oauth_token_expiry = :expiry
            WHERE id = :id
            """,
            {"token": encrypted_access, "expiry": expiry, "id": account_id},
        )


def _refresh_token(account_id: int, token_url: str, data: Dict[str, Any]) -> Optional[str]:
    """POST a refresh_token grant; persist the result; return the access token."""
    try:
        response = requests.post(token_url, data=data, timeout=30)
    except requests.exceptions.RequestException as e:
        logger.warning("OAuth refresh request failed for account %s: %s", account_id, e)
        return None

    if response.status_code != 200:
        logger.warning(
            "OAuth refresh failed for account %s (HTTP %s): %s",
            account_id,
            response.status_code,
            response.text[:300],
        )
        return None

    try:
        token_data = response.json()
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        new_refresh_token = token_data.get("refresh_token") or None
    except (ValueError, KeyError) as e:
        logger.warning("OAuth refresh returned an invalid payload for account %s: %s", account_id, e)
        return None

    _store_tokens(account_id, access_token, expires_in, new_refresh_token)
    return access_token


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

    return _refresh_token(account_id, token_url, data)


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
        "scope": O365_REFRESH_SCOPE
    }

    access_token = _refresh_token(account_id, token_url, data)
    if access_token is None:
        # Refresh tokens issued before the scope upgrade were granted narrower
        # permissions (Mail.Read), and the v2.0 refresh grant requires the
        # requested scope to be a subset of the original grant. Retry without a
        # scope so previously-authorized accounts keep syncing (read-only)
        # until the user re-authorizes with the wider scope.
        fallback_data = {k: v for k, v in data.items() if k != "scope"}
        access_token = _refresh_token(account_id, token_url, fallback_data)
    return access_token


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
