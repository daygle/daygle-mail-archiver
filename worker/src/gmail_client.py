"""Gmail API client for fetching emails"""
import requests
import base64
from typing import List, Dict, Optional
from datetime import datetime, timezone


class GmailClient:
    """Client for fetching emails from Gmail API"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://gmail.googleapis.com/gmail/v1/users/me"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
    
    def list_messages(self, page_token: Optional[str] = None, max_results: int = 100) -> Dict:
        """List messages from inbox"""
        params = {
            "maxResults": max_results,
            "labelIds": "INBOX"
        }
        if page_token:
            params["pageToken"] = page_token
        
        response = requests.get(
            f"{self.base_url}/messages",
            headers=self.headers,
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_message(self, email_id: str) -> Dict:
        """Get full message details including raw content"""
        response = requests.get(
            f"{self.base_url}/messages/{email_id}",
            headers=self.headers,
            params={"format": "raw"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_message_raw(self, email_id: str) -> bytes:
        """Get message in RFC822 format"""
        email_data = self.get_message(email_id)
        
        # Gmail returns base64url encoded raw message
        if "raw" in email_data:
            # Decode base64url to bytes
            raw_data = email_data["raw"]
            # Add padding if needed
            padding = 4 - len(raw_data) % 4
            if padding != 4:
                raw_data += "=" * padding
            # Replace URL-safe characters
            raw_data = raw_data.replace("-", "+").replace("_", "/")
            return base64.b64decode(raw_data)
        
        return b""
    
    def get_sync_token(self) -> Optional[str]:
        """Get current history ID for delta sync"""
        try:
            response = requests.get(
                f"{self.base_url}/profile",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            profile = response.json()
            return profile.get("historyId")
        except:
            return None
    
    def list_history(self, start_history_id: str) -> List[Dict]:
        """Get message history changes since start_history_id"""
        try:
            response = requests.get(
                f"{self.base_url}/history",
                headers=self.headers,
                params={"startHistoryId": start_history_id},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return data.get("history", [])
        except:
            return []
    
    def fetch_new_emails(self, last_sync_token: Optional[str] = None) -> List[str]:
        """
        Fetch new email IDs since last sync.
        Returns list of email IDs to process.
        """
        email_ids = []
        
        if last_sync_token:
            # Use history API for incremental sync
            history = self.list_history(last_sync_token)
            for h in history:
                if "messagesAdded" in h:
                    for msg in h["messagesAdded"]:
                        email_ids.append(msg["message"]["id"])
        else:
            # Full sync - get all emails
            page_token = None
            while True:
                result = self.list_messages(page_token=page_token)
                if "messages" in result:
                    for msg in result["messages"]:
                        email_ids.append(msg["id"])
                
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        
        return email_ids
    
    def delete_message(self, email_id: str) -> bool:
        """Delete a message by moving it to trash"""
        try:
            response = requests.post(
                f"{self.base_url}/messages/{email_id}/trash",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            return True
        except Exception:
            return False
