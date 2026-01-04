"""Office 365 Graph API client for fetching emails"""
import requests
import base64
from typing import List, Dict, Optional
from datetime import datetime, timezone


class O365Client:
    """Client for fetching emails from Microsoft Graph API"""
    
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://graph.microsoft.com/v1.0/me"
        self.headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }
    
    def list_messages(self, skip: int = 0, top: int = 100, delta_link: Optional[str] = None) -> Dict:
        """List messages from inbox"""
        if delta_link:
            # Use delta link for incremental sync
            response = requests.get(delta_link, headers=self.headers, timeout=30)
        else:
            params = {
                "$top": top,
                "$skip": skip,
                "$select": "id,subject,from,toRecipients,receivedDateTime,hasAttachments",
                "$orderby": "receivedDateTime DESC"
            }
            response = requests.get(
                f"{self.base_url}/mailFolders/inbox/messages",
                headers=self.headers,
                params=params,
                timeout=30
            )
        
        response.raise_for_status()
        return response.json()
    
    def get_message_mime(self, message_id: str) -> bytes:
        """Get message in MIME/RFC822 format"""
        response = requests.get(
            f"{self.base_url}/messages/{message_id}/$value",
            headers=self.headers,
            timeout=30
        )
        response.raise_for_status()
        return response.content
    
    def get_delta_link(self) -> Optional[str]:
        """Get delta link for incremental sync"""
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
            return data.get("@odata.deltaLink")
        except:
            return None
    
    def list_delta(self, delta_link: str) -> List[Dict]:
        """Get messages changed since delta link"""
        try:
            response = requests.get(delta_link, headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            return data.get("value", [])
        except:
            return []
    
    def fetch_new_messages(self, last_delta_link: Optional[str] = None) -> List[str]:
        """
        Fetch new message IDs since last sync.
        Returns list of message IDs to process.
        """
        message_ids = []
        
        if last_delta_link:
            # Use delta sync for incremental fetch
            messages = self.list_delta(last_delta_link)
            for msg in messages:
                # Delta may include deletions, check if message exists
                if "id" in msg and "@removed" not in msg:
                    message_ids.append(msg["id"])
        else:
            # Full sync - get all messages
            skip = 0
            top = 100
            while True:
                result = self.list_messages(skip=skip, top=top)
                if "value" in result:
                    for msg in result["value"]:
                        message_ids.append(msg["id"])
                    
                    # Check if there are more messages
                    if len(result["value"]) < top:
                        break
                    skip += top
                else:
                    break
        
        return message_ids
    
    def get_user_email(self) -> Optional[str]:
        """Get the user's email address"""
        try:
            response = requests.get(
                "https://graph.microsoft.com/v1.0/me",
                headers=self.headers,
                params={"$select": "mail,userPrincipalName"},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            return data.get("mail") or data.get("userPrincipalName")
        except:
            return None
    
    def delete_message(self, message_id: str) -> bool:
        """Delete a message"""
        try:
            response = requests.delete(
                f"{self.base_url}/messages/{message_id}",
                headers=self.headers,
                timeout=30
            )
            response.raise_for_status()
            return True
        except Exception:
            return False
