"""
Unit tests for the worker provider clients (Gmail API / Microsoft Graph).

These verify the incremental-sync robustness fixes: stale delta links /
history ids fall back to a full sync, and paginated responses are followed.
HTTP calls are mocked; no network or database access is required.

Run with:  python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest
import requests

WORKER_SRC = Path(__file__).resolve().parent.parent / "worker" / "src"
sys.path.insert(0, str(WORKER_SRC))

from gmail_client import GmailClient  # noqa: E402
from o365_client import O365Client  # noqa: E402


class _ErrResp:
    status_code = 0
    url = ""
    text = ""


class _FakeHTTPError(requests.exceptions.HTTPError):
    def __init__(self, status_code: int):
        super().__init__(f"{status_code} error")
        self.response = _ErrResp()
        self.response.status_code = status_code


class _Resp:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data
        self.content = content
        self.status_code = 200
        self.url = ""
        self.text = ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


class FakeGET:
    """Scripted requests.get: matches URLs by substring, supports queues."""

    def __init__(self):
        self.responses = {}

    def on(self, url_substring, *responses):
        self.responses[url_substring] = list(responses)

    def __call__(self, url, *args, **kwargs):
        # Query params are passed as a dict (serialized only at send time), so
        # match against both the URL and individual params (e.g. "$skip=100").
        params = kwargs.get("params") or {}
        for key, queue in self.responses.items():
            matched = key in url
            if not matched and "=" in key:
                pk, pv = key.split("=", 1)
                matched = str(params.get(pk)) == pv
            if matched:
                if not queue:
                    raise AssertionError(f"No more scripted responses for {url}")
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        raise AssertionError(f"Unexpected URL: {url} params: {params}")


@pytest.fixture()
def fake_get(monkeypatch):
    fg = FakeGET()
    monkeypatch.setattr(requests, "get", fg)
    return fg


# ---------------------------------------------------------------------------
# O365 (Microsoft Graph) client
# ---------------------------------------------------------------------------


def test_o365_delta_fetch_returns_message_ids(fake_get):
    client = O365Client("token")
    fake_get.on(
        "delta",
        _Resp({"value": [{"id": "m1"}, {"id": "m2", "@removed": {"reason": "deleted"}}]}),
    )
    ids = client.fetch_new_emails("https://graph.example/delta-link")
    assert ids == ["m1"]  # @removed entries are skipped


def test_o365_delta_pagination(fake_get):
    client = O365Client("token")
    fake_get.on(
        "first-page",
        _Resp({"value": [{"id": "a"}], "@odata.nextLink": "https://graph.example/second-page"}),
    )
    fake_get.on("second-page", _Resp({"value": [{"id": "b"}]}))
    messages = client.list_delta("https://graph.example/first-page")
    assert [m["id"] for m in messages] == ["a", "b"]


def test_o365_expired_delta_falls_back_to_full_sync(fake_get):
    """An expired delta link (410 Gone) must not silently stall the account."""
    client = O365Client("token")
    fake_get.on("delta-link", _FakeHTTPError(410))
    fake_get.on(
        "/messages",
        _Resp({"value": [{"id": "m1"}, {"id": "m2"}]}),
    )
    ids = client.fetch_new_emails("https://graph.example/delta-link")
    assert ids == ["m1", "m2"]


def test_o365_transient_delta_error_propagates(fake_get):
    """Non-stale errors should propagate so the worker can log and retry."""
    client = O365Client("token")
    fake_get.on("delta-link", _FakeHTTPError(500))
    with pytest.raises(requests.exceptions.HTTPError):
        client.fetch_new_emails("https://graph.example/delta-link")


def test_o365_full_sync_paginates(fake_get):
    """Full sync walks a fresh delta session (not $skip, which caps at 5000)."""
    client = O365Client("token")
    fake_get.on(
        "messages/delta",
        _Resp({"value": [{"id": f"m{i}"} for i in range(100)], "@odata.nextLink": "https://graph.example/page2"}),
    )
    fake_get.on("page2", _Resp({"value": [{"id": "last"}]}))
    ids = client.fetch_new_emails(None)
    assert len(ids) == 101
    assert ids[-1] == "last"


def test_o365_full_sync_captures_delta_link(fake_get):
    """The final page's @odata.deltaLink becomes the next sync token."""
    client = O365Client("token")
    fake_get.on(
        "messages/delta",
        _Resp({"value": [{"id": "m1"}], "@odata.deltaLink": "https://graph.example/new-delta"}),
    )
    ids = client.fetch_new_emails(None)
    assert ids == ["m1"]
    # No extra HTTP round trip: get_delta_link returns the captured link.
    assert client.get_delta_link() == "https://graph.example/new-delta"


def test_o365_delta_captures_delta_link(fake_get):
    """Incremental delta walks also capture the continuation link."""
    client = O365Client("token")
    fake_get.on(
        "old-delta",
        _Resp({"value": [{"id": "m1"}], "@odata.deltaLink": "https://graph.example/fresh-delta"}),
    )
    ids = client.fetch_new_emails("https://graph.example/old-delta")
    assert ids == ["m1"]
    assert client.get_delta_link() == "https://graph.example/fresh-delta"


# ---------------------------------------------------------------------------
# Gmail client
# ---------------------------------------------------------------------------


def test_gmail_history_fetch_returns_message_ids(fake_get):
    client = GmailClient("token")
    fake_get.on(
        "/history",
        _Resp({"history": [{"messagesAdded": [{"message": {"id": "g1"}}]}]}),
    )
    ids = client.fetch_new_emails("12345")
    assert ids == ["g1"]


def test_gmail_history_pagination(fake_get):
    client = GmailClient("token")
    fake_get.on(
        "/history",
        _Resp(
            {
                "history": [{"messagesAdded": [{"message": {"id": "g1"}}]}],
                "nextPageToken": "tok2",
            }
        ),
        _Resp({"history": [{"messagesAdded": [{"message": {"id": "g2"}}]}]}),
    )
    history = client.list_history("12345")
    added = []
    for h in history:
        added.extend(msg["message"]["id"] for msg in h.get("messagesAdded", []))
    assert added == ["g1", "g2"]


def test_gmail_empty_history_does_not_full_sync(fake_get):
    """An empty history (no changes) must NOT trigger an expensive full sync."""
    client = GmailClient("token")
    fake_get.on("/history", _Resp({"history": []}))
    ids = client.fetch_new_emails("12345")
    assert ids == []
    # No list_messages call was made (no URL matched beyond /history)


def test_gmail_stale_history_falls_back_to_full_sync(fake_get):
    """A stale history id (404) must fall back to a full sync."""
    client = GmailClient("token")
    fake_get.on("/history", _FakeHTTPError(404))
    fake_get.on(
        "/messages",
        _Resp({"messages": [{"id": "g1"}, {"id": "g2"}]}),
    )
    ids = client.fetch_new_emails("stale-history-id")
    assert ids == ["g1", "g2"]


def test_gmail_get_message_raw_base64url(fake_get):
    import base64

    raw_bytes = b"From: test@example.com\r\nSubject: hi\r\n\r\nbody"
    encoded = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
    client = GmailClient("token")
    fake_get.on(
        "/messages/abc",
        _Resp({"id": "abc", "raw": encoded}),
    )
    assert client.get_message_raw("abc") == raw_bytes
