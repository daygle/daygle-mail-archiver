"""
ClamAV scanner module for virus scanning of emails.
"""
import time

import pyclamd
from typing import Optional, Tuple
from datetime import datetime, timezone
from db import query, execute


def _scan_result_details(result):
    """Return the status/name pair from pyclamd's scan_stream response."""
    if isinstance(result, dict):
        result = next(iter(result.values()), None)
    if isinstance(result, (tuple, list)):
        status = result[0] if result else None
        name = result[1] if len(result) > 1 else None
        return status, name
    return None, None


def log_warning(message: str, details: str = ""):
    """Log warning message to database."""
    try:
        execute(
            """
            INSERT INTO logs (timestamp, level, source, message, details)
            VALUES (:ts, :level, :source, :message, :details)
            """,
            {
                "ts": datetime.now(timezone.utc),
                "level": "warning",
                "source": "ClamAV",
                "message": message[:500],
                "details": details[:4000],
            },
        )
    except Exception:
        # If logging fails, just continue - don't break email processing
        pass


def log_info(message: str, details: str = ""):
    """Log info message to database."""
    try:
        execute(
            """
            INSERT INTO logs (timestamp, level, source, message, details)
            VALUES (:ts, :level, :source, :message, :details)
            """,
            {
                "ts": datetime.now(timezone.utc),
                "level": "info",
                "source": "ClamAV",
                "message": message[:500],
                "details": details[:4000],
            },
        )
    except Exception:
        # If logging fails, just continue - don't break email processing
        pass


def create_alert(alert_type: str, title: str, message: str, details: str = None, trigger_key: str = None):
    """
    Create a system alert (ClamAV-side implementation).

    Args:
        alert_type: Type of alert ('error', 'warning', 'info', 'success') - can be overridden by trigger_key
        title: Alert title
        message: Alert message
        details: Optional detailed information
        trigger_key: Optional trigger key to check if alert should be created and get severity from
    """
    # If trigger_key is provided, look up the configured alert_type and check if enabled
    actual_alert_type = alert_type
    if trigger_key:
        try:
            result = query("SELECT alert_type, enabled FROM alert_triggers WHERE trigger_key = :key", {"key": trigger_key}).mappings().first()
            if result:
                if not result["enabled"]:
                    # Trigger is disabled, don't create alert
                    return
                # Use the configured alert_type from the database
                actual_alert_type = result["alert_type"]
        except Exception:
            # If we can't check the trigger, use the provided alert_type
            pass

    try:
        execute("""
            INSERT INTO alerts (alert_type, title, message, details)
            VALUES (:alert_type, :title, :message, :details)
        """, {
            "alert_type": actual_alert_type,
            "title": title,
            "message": message,
            "details": details
        })
    except Exception as e:
        # If alert creation fails, just log it - don't break email processing
        log_warning(f"Failed to create alert '{title}': {str(e)}")


class ClamAVScanner:
    """ClamAV virus scanner for email content."""

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
        self._settings_error = False
        self._action = 'quarantine'
        self._failure_count = 0
        self._first_failure_time = None
        # Only alert on a *sustained* outage. Brief blips - most commonly clamd
        # refusing connections for a minute or two while it reloads its signature
        # database after a freshclam update - recover well within this window and
        # are suppressed (no unavailable/recovered alert pair). Tunable via the
        # 'clamav_failure_grace_seconds' setting.
        self._failure_grace_seconds = 300
        self._failure_alert_threshold = 2
        self._settings_refresh_interval = 30.0
        self._last_settings_load = 0.0
        self._load_settings()
        self._last_settings_load = time.monotonic()


    def _load_settings(self):
        """Load ClamAV settings from database."""
        previous_endpoint = (self.host, self.port)
        was_settings_error = self._settings_error
        try:
            # Fetch every clamav_* setting. Previously this only selected four keys,
            # so clamav_quarantine_in_db, clamav_quarantine_retention_days,
            # clamav_max_file_size and clamav_quarantine_encrypt were read from the
            # dict but never present, silently falling back to defaults (e.g.
            # quarantine encryption could never be enabled from settings).
            settings = query(
                """
                SELECT key, value FROM settings
                WHERE key LIKE 'clamav_%'
                """
            ).mappings().all()

            settings_dict = {row['key']: row['value'] for row in settings}

            # Load settings. A successful reload clears a previous transient
            # database/settings error so the worker can recover without restart.
            self._enabled = str(settings_dict.get('clamav_enabled', 'true')).lower() == 'true'
            self.host = settings_dict.get('clamav_host', self.host)
            self.port = int(settings_dict.get('clamav_port', self.port))
            self._action = settings_dict.get('clamav_action', 'quarantine')
            self._settings_error = False
            if (self.host, self.port) != previous_endpoint:
                self._scanner = None
            self._quarantine_in_db = settings_dict.get('clamav_quarantine_in_db', 'true').lower() == 'true'
            try:
                self._quarantine_retention_days = int(settings_dict.get('clamav_quarantine_retention_days', '90'))
            except Exception:
                self._quarantine_retention_days = 90
            try:
                # Allow overriding max scan size from settings (bytes)
                self.MAX_SCAN_SIZE = int(settings_dict.get('clamav_max_file_size', self.MAX_SCAN_SIZE))
            except Exception:
                pass
            try:
                # How long a ClamAV outage must persist before alerting. Keeps brief
                # signature-reload blips from generating unavailable/recovered noise.
                self._failure_grace_seconds = int(
                    settings_dict.get('clamav_failure_grace_seconds', self._failure_grace_seconds)
                )
            except Exception:
                pass
            # Optional application-level encryption for quarantined raw emails
            self._quarantine_encrypt = settings_dict.get('clamav_quarantine_encrypt', 'false').lower() == 'true'
            self._quarantine_key = None
            if self._quarantine_encrypt:
                try:
                    from config import get_config
                    key = get_config('CLAMAV_QUARANTINE_KEY')
                    if key:
                        from cryptography.fernet import Fernet
                        self._quarantine_key = Fernet(key.encode())
                    else:
                        # No key provided - disable encryption with warning
                        log_warning('clamav_quarantine_encrypt set but CLAMAV_QUARANTINE_KEY not found in config')
                        self._quarantine_encrypt = False
                except Exception as e:
                    log_warning('Failed to initialise quarantine encryption', str(e))
                    self._quarantine_encrypt = False
        except Exception as e:
            # A settings/database failure must not silently turn scanning off.
            # Keep the scanner in a not-ready state so callers fail closed and
            # retry the provider message after configuration is available.
            log_warning("Could not load ClamAV settings from database", str(e))
            self._settings_error = True
            if not was_settings_error:
                create_alert(
                    'error',
                    'ClamAV Configuration Error',
                    'Failed to load virus scanning settings from database',
                    f'Error: {str(e)}. Incoming emails will wait for a completed scan.',
                    'clamav_config_error'
                )
            self._enabled = False

    def refresh_settings(self, force: bool = False):
        """Refresh settings periodically so UI changes apply without a restart."""
        now = time.monotonic()
        if force or now - self._last_settings_load >= self._settings_refresh_interval:
            self._load_settings()
            self._last_settings_load = now

    def requires_scan(self) -> bool:
        """Whether an email must be scanned before it may be archived."""
        self.refresh_settings()
        return self._enabled or self._settings_error

    def _get_clamav_status(self) -> str:
        """Get the last known ClamAV availability status from the database."""
        try:
            row = query(
                "SELECT value FROM settings WHERE key = 'clamav_status'"
            ).mappings().first()
            return row['value'] if row else 'unknown'
        except Exception as e:
            log_warning("Could not read ClamAV status from database", str(e))
            return 'unknown'

    def _set_clamav_status(self, status: str):
        """Persist the ClamAV availability status to the database."""
        try:
            execute(
                "INSERT INTO settings (key, value) VALUES ('clamav_status', :status) "
                "ON CONFLICT (key) DO UPDATE SET value = :status",
                {"status": status}
            )
        except Exception as e:
            log_warning("Could not persist ClamAV status to database", str(e))

    def _record_connection_failure(self) -> bool:
        """Track repeated ClamAV connection failures and return whether an alert should be sent.

        An alert is only warranted once the outage has *persisted* for the grace
        period (and produced more than a single failure). This deliberately avoids
        alerting on short-lived blips such as clamd reloading its signature
        database after a freshclam update, which would otherwise produce a noisy
        "unavailable" then "recovered" pair every time definitions update.
        """
        now = datetime.now(timezone.utc)
        if self._first_failure_time is None:
            self._first_failure_time = now
            self._failure_count = 1
        else:
            self._failure_count += 1

        elapsed = (now - self._first_failure_time).total_seconds()
        return (
            elapsed >= self._failure_grace_seconds
            and self._failure_count >= self._failure_alert_threshold
        )

    def _reset_connection_failure(self):
        """Reset the transient connection failure tracking."""
        self._failure_count = 0
        self._first_failure_time = None

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
                self._reset_connection_failure()
                # Recover: if ClamAV was previously unavailable, fire a recovery alert
                if self._get_clamav_status() == 'unavailable':
                    log_info(f"ClamAV service at {self.host}:{self.port} is available again")
                    create_alert(
                        'info',
                        'ClamAV Service Recovered',
                        f'ClamAV daemon at {self.host}:{self.port} is available again',
                        'Virus scanning has resumed.',
                        'clamav_recovered'
                    )
                self._set_clamav_status('available')
                return scanner
            else:
                # ping() returned False - only alert after repeated failures or a grace period
                should_alert = self._record_connection_failure()
                log_warning(f"ClamAV ping failed at {self.host}:{self.port}")
                if should_alert and self._get_clamav_status() != 'unavailable':
                    create_alert(
                        'error',
                        'ClamAV Connection Failed',
                        f'Cannot connect to ClamAV daemon at {self.host}:{self.port}',
                        'Virus scanning is unavailable. Check ClamAV service status.',
                        'clamav_unavailable'
                    )
                    self._set_clamav_status('unavailable')
                return None
        except Exception as e:
            log_warning(f"Could not connect to ClamAV at {self.host}:{self.port}", str(e))
            should_alert = self._record_connection_failure()
            if should_alert and self._get_clamav_status() != 'unavailable':
                create_alert(
                    'error',
                    'ClamAV Service Unavailable',
                    f'Failed to establish connection to ClamAV daemon',
                    f'Host: {self.host}:{self.port}, Error: {str(e)}. Virus scanning is disabled.',
                    'clamav_unavailable'
                )
                self._set_clamav_status('unavailable')
            # Reset cached scanner on connection failure
            self._scanner = None

        return None

    def is_enabled(self) -> bool:
        """Check if virus scanning is enabled."""
        self.refresh_settings()
        return self._enabled

    def get_action(self) -> str:
        """Get the configured action for virus detection."""
        return self._action

    def scan(self, email_bytes: bytes) -> Tuple[bool, Optional[str], Optional[datetime], bool]:
        """
        Scan email content for viruses.

        Args:
            email_bytes: Raw email content as bytes

        Returns:
            Tuple of (virus_detected, virus_name, scan_timestamp, scanned).
            ``scanned`` is True ONLY when a scan actually ran against clamd.
            It is False when scanning is disabled, the email exceeds the size
            limit, or clamd is unreachable - callers must not record
            ``virus_scanned = True`` in those cases, or the unscanned filter
            and scan-coverage metrics would report emails as scanned that
            never were.
        """
        self.refresh_settings()
        if not self._enabled:
            return False, None, None, False

        # Check email size - skip scanning very large emails
        email_size = len(email_bytes)
        if email_size > self.MAX_SCAN_SIZE:
            log_warning(
                f"Email too large to scan ({email_size} bytes, max {self.MAX_SCAN_SIZE})",
                "Skipping virus scan for oversized email"
            )
            return False, None, None, False

        scanner = self._connect()
        if not scanner:
            # If we can't connect, log warning and allow email through.
            # The alert was already fired by _connect() on the first failure.
            log_warning("ClamAV scanner not available, skipping virus scan")
            return False, None, None, False

        scan_timestamp = datetime.now(timezone.utc)
        try:
            # Scan the email content
            result = scanner.scan_stream(email_bytes)

            if result is None:
                # No virus detected
                return False, None, scan_timestamp, True

            # pyclamd returns {"stream": ("FOUND", "virus_name")} for a
            # detection; older versions/tests may return the tuple directly.
            status, virus_name = _scan_result_details(result)
            normalized_status = status.strip().upper() if isinstance(status, str) else None
            if normalized_status == 'FOUND':
                return True, virus_name or 'Unknown', scan_timestamp, True
            if normalized_status in {'OK', 'CLEAN'}:
                return False, None, scan_timestamp, True

            # Never call an unrecognised clamd response a clean scan. This keeps
            # the ingestion path fail-closed if pyclamd/clamd changes its reply.
            log_warning("ClamAV returned an invalid scan response", repr(result))
            return False, None, None, False

        except Exception as e:
            log_warning("Error during virus scan", str(e))
            create_alert(
                'warning',
                'ClamAV Scan Error',
                'An error occurred during virus scanning',
                f'Error: {str(e)}. Email was allowed through without scanning.',
                'clamav_error'
            )
            # Reset cached scanner on error to force reconnection next time
            self._scanner = None
            # On error, allow email through but log the issue
            return False, None, None, False
