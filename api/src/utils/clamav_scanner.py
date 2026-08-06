"""
Simplified ClamAV scanner for API import functionality.
"""
import os
import pyclamd
from typing import Optional, Tuple
from datetime import datetime, timezone

from .db import query
from .logger import log


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
        """Load ClamAV settings from database, with environment variable fallbacks."""
        try:
            settings = query(
                """
                SELECT key, value FROM settings
                WHERE key IN ('clamav_enabled', 'clamav_host', 'clamav_port', 'clamav_max_file_size')
                """
            ).mappings().all()

            settings_dict = {s['key']: s['value'] for s in settings}

            self._enabled = settings_dict.get('clamav_enabled', 'true').lower() == 'true'
            self.host = settings_dict.get('clamav_host', os.getenv('CLAMAV_HOST', 'clamav'))
            self.port = int(settings_dict.get('clamav_port', os.getenv('CLAMAV_PORT', '3310')))
            try:
                # Honour the globally-configured scan size limit (bytes)
                self.MAX_SCAN_SIZE = int(settings_dict.get('clamav_max_file_size', self.MAX_SCAN_SIZE))
            except (ValueError, TypeError):
                pass

        except Exception as e:
            log("warning", "ClamAV", f"Failed to load ClamAV settings: {e}", "")
            # Use environment variables or defaults if settings can't be loaded
            self._enabled = os.getenv('CLAMAV_ENABLED', 'true').lower() == 'true'
            self.host = os.getenv('CLAMAV_HOST', 'clamav')
            self.port = int(os.getenv('CLAMAV_PORT', '3310'))

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

    def scan(self, email_bytes: bytes) -> Tuple[bool, Optional[str], Optional[datetime], bool]:
        """
        Scan email content for viruses.

        Args:
            email_bytes: Raw email content as bytes

        Returns:
            Tuple of (virus_detected, virus_name, scan_timestamp, scanned).
            ``scanned`` is True ONLY when a scan actually ran against clamd.
            It is False when scanning is disabled, the email exceeds the size
            limit, or clamd is unreachable - in those cases the caller must NOT
            record ``virus_scanned = True`` (the email was not scanned, and
            claiming otherwise would corrupt the unscanned filter and the scan
            coverage metrics).
        """
        if not self._enabled:
            return False, None, None, False

        # Check email size - skip scanning very large emails
        email_size = len(email_bytes)
        if email_size > self.MAX_SCAN_SIZE:
            log("warning", "ClamAV", f"Email too large to scan ({email_size} bytes, max {self.MAX_SCAN_SIZE})", "")
            return False, None, None, False

        scanner = self._connect()
        if not scanner:
            # If we can't connect, log warning and allow email through
            log("warning", "ClamAV", "ClamAV scanner not available, skipping virus scan", "")
            return False, None, None, False

        scan_timestamp = datetime.now(timezone.utc)
        try:
            # Scan the email content
            result = scanner.scan_stream(email_bytes)

            if result is None:
                # No virus detected
                return False, None, scan_timestamp, True

            # Virus detected - result format: ('FOUND', 'virus_name')
            if result and result[0] == 'FOUND':
                virus_name = result[1] if len(result) > 1 else 'Unknown'
                return True, virus_name, scan_timestamp, True

            return False, None, scan_timestamp, True

        except Exception as e:
            log("warning", "ClamAV", f"Error during virus scan: {e}", "")
            # Reset cached scanner on error to force reconnection next time
            self._scanner = None
            # On error, allow email through but log the issue
            return False, None, None, False