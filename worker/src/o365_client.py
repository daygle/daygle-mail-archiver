"""Office 365 Graph API client for fetching emails"""
import requests
from typing import List, Dict, Optional


class O365Client:
    """Client for fetching emails from Microsoft Graph API"""

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://graph.microsoft.com/v1.0/me"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
        # The delta link of the most recently walked delta session. Set whenever
        # a delta or full sync is performed, so the worker can persist the token
        # without an extra round trip.
        self._last_delta_link = None

    def get_message_mime(self, email_id: str) -> bytes:
        """Get message in MIME/RFC822 format"""
        response = requests.get(
            f"{self.base_url}/messages/{email_id}/$value",
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()
        return response.content

    def get_delta_link(self) -> Optional[str]:
        """Get the delta link to persist for the next incremental sync.

        Prefers the delta link captured from the most recently walked delta
        session (it continues exactly where that sync left off); otherwise
        bootstraps a fresh delta session.
        """
        if self._last_delta_link:
            return self._last_delta_link
        try:
            params = {
                "$select": "id,receivedDateTime",
                "$top": 1
            }
            response = requests.get(
                f"{self.base_url}/mailFolders/inbox/messages/delta",
                headers=self.headers,
                params=params,
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            self._last_delta_link = data.get("@odata.deltaLink")
            return self._last_delta_link
        except Exception:
            return None

    def list_delta(self, delta_link: str) -> List[Dict]:
        """Get messages changed since delta link (follows @odata.nextLink pages).

        Raises requests.HTTPError on failure so callers can distinguish an
        expired delta link (HTTP 410) from transient errors.
        """
        messages = []
        url = delta_link
        while url:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            messages.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            if not url:
                # The final page of a delta session carries the link that
                # continues the session; remember it for get_delta_link().
                self._last_delta_link = data.get("@odata.deltaLink") or delta_link
        return messages
    def fetch_new_emails(self, last_delta_link: Optional[str] = None) -> List[str]:
        """
        Fetch new email IDs using delta query.
        Returns list of email IDs to process.
        """
        email_ids = []

        if last_delta_link:
            # Use delta sync for incremental fetch
            stale = False
            try:
                messages = self.list_delta(last_delta_link)
            except requests.exceptions.HTTPError as e:
                # Delta links expire; fall back to a full sync so mail keeps flowing.
                if e.response is not None and e.response.status_code in (404, 410):
                    stale = True
                else:
                    raise
            if not stale:
                for msg in messages:
                    # Delta may include deletions, check if message exists
                    if "id" in msg and "@removed" not in msg:
                        email_ids.append(msg["id"])
                return email_ids
            # expired/stale delta link -> fall through to a full sync below

        # Full sync: walk a fresh delta session. Unlike $skip/$top pagination
        # this is O(changes), has no 5000-message skip limit (which would stall
        # accounts with larger inboxes), and yields the next delta link for free.
        url = f"{self.base_url}/mailFolders/inbox/messages/delta?$select=id&$top=100"
        while url:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            for msg in data.get("value", []):
                # Delta may include deletions, check if message exists
                if "id" in msg and "@removed" not in msg:
                    email_ids.append(msg["id"])
            url = data.get("@odata.nextLink")
            if not url:
                self._last_delta_link = data.get("@odata.deltaLink")

        return email_ids

    def delete_message(self, email_id: str) -> bool:
        """Delete a message"""
        try:
            response = requests.delete(
                f"{self.base_url}/messages/{email_id}",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            return True
        except Exception:
            return False
