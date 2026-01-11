"""
Simplified ClamAV scanner for API import functionality.
"""
import pyclamd
from typing import Optional, Tuple
from datetime import datetime, timezone

from utils.db import query
from utils.logger import log


class ClamAVScanner:
    """Simplified ClamAV virus scanner for email import."""

    # Maximum email size to scan (100MB) - very large emails are skipped
    MAX_SCAN_SIZE = 100 * 1024 * 1024

    def __init__(self, host: str = 'clamav', port: int = 3310):
        """
        Initialise ClamAV scanner.

        Args:
            host: ClamAV daemon hostname
            port: ClamAV daemon port
        """
        self.host = host
        self.port = port
        self._scanner = None
        self._enabled = True
        self._load_settings()

    def _load_settings(self):
        """Load ClamAV settings from database."""
        try:
            settings = query(
                """
                SELECT key, value FROM settings
                WHERE key IN ('clamav_enabled', 'clamav_host', 'clamav_port')
                """
            ).mappings().all()

            settings_dict = {s['key']: s['value'] for s in settings}

            self._enabled = settings_dict.get('clamav_enabled', 'true').lower() == 'true'
            self.host = settings_dict.get('clamav_host', 'clamav')
            self.port = int(settings_dict.get('clamav_port', '3310'))

        except Exception as e:
            log("warning", "ClamAV", f"Failed to load ClamAV settings: {e}", "")
            # Use defaults if settings can't be loaded
            self._enabled = True
            self.host = 'clamav'
            self.port = 3310

    def is_enabled(self) -> bool:
        """Check if virus scanning is enabled."""
        return self._enabled

    def _connect(self):
        """Connect to ClamAV daemon."""
        if self._scanner:
            return self._scanner

        try:
            self._scanner = pyclamd.ClamdNetworkSocket(host=self.host, port=self.port)
            # Test connection
            self._scanner.ping()
            return self._scanner
        except Exception as e:
            log("warning", "ClamAV", f"Failed to connect to ClamAV daemon: {e}", "")
            self._scanner = None
            return None

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

        # Check email size - skip scanning very large emails
        email_size = len(email_bytes)
        if email_size > self.MAX_SCAN_SIZE:
            log("warning", "ClamAV", f"Email too large to scan ({email_size} bytes, max {self.MAX_SCAN_SIZE})", "")
            return False, None, scan_timestamp

        scanner = self._connect()
        if not scanner:
            # If we can't connect, log warning and allow email through
            log("warning", "ClamAV", "ClamAV scanner not available, skipping virus scan", "")
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
            log("warning", "ClamAV", f"Error during virus scan: {e}", "")
            # Reset cached scanner on error to force reconnection next time
            self._scanner = None
            # On error, allow email through but log the issue
            return False, None, scan_timestamp</content>
<parameter name="filePath">g:\Git\daygle-mail-archiver\api\src\utils\clamav_scanner.py