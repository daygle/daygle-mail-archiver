"""
Unit tests for the OAuth flow helpers (CSRF state round-trip and redirect URI
construction). No database or network access is required.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes import oauth  # noqa: E402


class FakeRequest:
    def __init__(self, session=None, url_for_result="http://api:8000/oauth/gmail/callback/5"):
        self.session = session if session is not None else {}
        self._url_for_result = url_for_result

    def url_for(self, name, **kwargs):
        assert name == "gmail_oauth_callback"
        return self._url_for_result


# ---------------------------------------------------------------------------
# CSRF state
# ---------------------------------------------------------------------------


def test_state_round_trip():
    req = FakeRequest()
    state = oauth._new_oauth_state(req, "gmail", 5)
    assert oauth._verify_oauth_state(req, "gmail", 5, state) is True
    # State is single-use: a second attempt must fail
    assert oauth._verify_oauth_state(req, "gmail", 5, state) is False


def test_state_mismatch_rejected():
    req = FakeRequest()
    oauth._new_oauth_state(req, "gmail", 5)
    assert oauth._verify_oauth_state(req, "gmail", 5, "attacker-controlled") is False
    # The key is cleared even on mismatch
    assert oauth._verify_oauth_state(req, "gmail", 5, None) is False


def test_state_without_prior_start_rejected():
    assert oauth._verify_oauth_state(FakeRequest(), "gmail", 5, "anything") is False


def test_state_is_random():
    req = FakeRequest()
    assert oauth._new_oauth_state(req, "gmail", 5) != oauth._new_oauth_state(req, "gmail", 5)


# ---------------------------------------------------------------------------
# Redirect URI construction
# ---------------------------------------------------------------------------


def test_redirect_uri_uses_public_base_url(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "get_config",
        lambda key, default=None: "https://archive.example.com" if key == "PUBLIC_BASE_URL" else default,
    )
    uri = oauth._build_redirect_uri(FakeRequest(), "gmail", 5)
    assert uri == "https://archive.example.com/oauth/gmail/callback/5"


def test_redirect_uri_falls_back_to_request_url(monkeypatch):
    monkeypatch.setattr(
        oauth,
        "get_config",
        lambda key, default=None: None if key == "PUBLIC_BASE_URL" else default,
    )
    req = FakeRequest(url_for_result="http://api:8000/oauth/gmail/callback/5")
    uri = oauth._build_redirect_uri(req, "gmail", 5)
    assert uri == "http://api:8000/oauth/gmail/callback/5"


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


def test_gmail_scope_includes_modify():
    # gmail.modify (not just readonly) is required to move processed messages
    # to Trash ("delete after processing").
    assert "gmail.modify" in oauth.GMAIL_SCOPE


def test_o365_scope_includes_readwrite():
    # Mail.ReadWrite (not just Mail.Read) is required to delete processed
    # messages via the Graph API.
    assert "Mail.ReadWrite" in oauth.O365_SCOPE
    assert "offline_access" in oauth.O365_SCOPE
