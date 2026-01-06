"""
ClamAV scanner module for virus scanning of emails.
"""
import pyclamd
from typing import Optional, Tuple
from datetime import datetime, timezone
from db import query


class ClamAVScanner:
    """ClamAV virus scanner for email content."""
    
    def __init__(self, host: str = 'clamav', port: int = 3310):
        """
        Initialize ClamAV scanner.
        
        Args:
            host: ClamAV daemon hostname
            port: ClamAV daemon port
        """
        self.host = host
        self.port = port
        self._scanner = None
        self._enabled = True
        self._action = 'quarantine'
        self._load_settings()
    
    def _load_settings(self):
        """Load ClamAV settings from database."""
        try:
            settings = query(
                """
                SELECT key, value FROM settings 
                WHERE key IN ('clamav_enabled', 'clamav_host', 'clamav_port', 'clamav_action')
                """
            ).mappings().all()
            
            settings_dict = {row['key']: row['value'] for row in settings}
            
            # Load settings
            self._enabled = settings_dict.get('clamav_enabled', 'true').lower() == 'true'
            self.host = settings_dict.get('clamav_host', self.host)
            self.port = int(settings_dict.get('clamav_port', self.port))
            self._action = settings_dict.get('clamav_action', 'quarantine')
        except Exception as e:
            # If we can't load settings, use defaults and disable scanning
            print(f"Warning: Could not load ClamAV settings from database: {e}")
            self._enabled = False
    
    def _connect(self) -> Optional[pyclamd.ClamdNetworkSocket]:
        """
        Connect to ClamAV daemon.
        
        Returns:
            ClamAV connection object or None if connection fails
        """
        if self._scanner:
            return self._scanner
        
        try:
            scanner = pyclamd.ClamdNetworkSocket(host=self.host, port=self.port)
            # Test connection
            if scanner.ping():
                self._scanner = scanner
                return scanner
        except Exception as e:
            print(f"Warning: Could not connect to ClamAV at {self.host}:{self.port}: {e}")
        
        return None
    
    def is_enabled(self) -> bool:
        """Check if virus scanning is enabled."""
        return self._enabled
    
    def get_action(self) -> str:
        """Get the configured action for virus detection."""
        return self._action
    
    def scan(self, email_bytes: bytes) -> Tuple[bool, Optional[str], datetime]:
        """
        Scan email content for viruses.
        
        Args:
            email_bytes: Raw email content as bytes
            
        Returns:
            Tuple of (virus_detected: bool, virus_name: Optional[str], scan_timestamp: datetime)
        """
        scan_timestamp = datetime.now(timezone.utc)
        
        if not self._enabled:
            return False, None, scan_timestamp
        
        scanner = self._connect()
        if not scanner:
            # If we can't connect, log warning and allow email through
            print("Warning: ClamAV scanner not available, skipping virus scan")
            return False, None, scan_timestamp
        
        try:
            # Scan the email content
            result = scanner.scan_stream(email_bytes)
            
            if result is None:
                # No virus detected
                return False, None, scan_timestamp
            
            # Virus detected - result format: ('FOUND', 'virus_name')
            if result and result[0] == 'FOUND':
                virus_name = result[1] if len(result) > 1 else 'Unknown'
                return True, virus_name, scan_timestamp
            
            return False, None, scan_timestamp
            
        except Exception as e:
            print(f"Warning: Error during virus scan: {e}")
            # On error, allow email through but log the issue
            return False, None, scan_timestamp
    
    def reload_settings(self):
        """Reload settings from database."""
        self._load_settings()
