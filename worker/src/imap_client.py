import imaplib
import ssl
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ImapConnection:
    """
    IMAP connection handler with SSL, STARTTLS, and SASL PLAIN authentication support.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        use_ssl: bool,
        require_starttls: bool,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.require_starttls = require_starttls
        self.conn: Optional[imaplib.IMAP4] = None
        self._connected = False

    def _try_sasl_plain(self, authzid: str, authcid: str, password: str) -> None:
        """Try SASL PLAIN authentication with specified authzid and authcid."""
        auth_string = base64.b64encode(
            f"{authzid}\0{authcid}\0{password}".encode("utf-8")
        ).decode("ascii")

        def auth_plain(_):
            return auth_string

        self.conn.authenticate("PLAIN", auth_plain)

    def _normalize_capabilities(self, caps) -> bytes:
        """Normalize IMAP capabilities to a flat bytes string."""
        normalized_caps = []
        for c in caps:
            if isinstance(c, list):
                for sub in c:
                    if isinstance(sub, bytes):
                        normalized_caps.append(sub)
                    else:
                        normalized_caps.append(str(sub).encode("utf-8"))
            else:
                if isinstance(c, bytes):
                    normalized_caps.append(c)
                else:
                    normalized_caps.append(str(c).encode("utf-8"))
        return b" ".join(normalized_caps)

    def __enter__(self) -> imaplib.IMAP4:
        if self._connected:
            raise RuntimeError("Connection already established")

        try:
            # SSL connection
            if self.use_ssl:
                logger.debug(f"Connecting to {self.host}:{self.port} with SSL")
                context = ssl.create_default_context()
                self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
                self.conn.login(self.username, self.password)
                self._connected = True
                return self.conn

            # Non-SSL connection
            logger.debug(f"Connecting to {self.host}:{self.port} without SSL")
            self.conn = imaplib.IMAP4(self.host, self.port)

            # STARTTLS upgrade
            if self.require_starttls:
                logger.debug("Upgrading connection with STARTTLS")
                self.conn.starttls()

                # Check capabilities for authentication methods
                caps = self.conn.capability()
                caps_flat = self._normalize_capabilities(caps)

                # LOGIN allowed?
                if b"AUTH=LOGIN" in caps_flat:
                    logger.debug("Using LOGIN authentication")
                    self.conn.login(self.username, self.password)
                    self._connected = True
                    return self.conn

                # SASL PLAIN allowed?
                if b"AUTH=PLAIN" in caps_flat:
                    logger.debug("Using SASL PLAIN authentication")
                    # Try SASL PLAIN variants in order

                    # Variant 1: authzid="", authcid=username
                    try:
                        self._try_sasl_plain("", self.username, self.password)
                        self._connected = True
                        return self.conn
                    except Exception as e:
                        logger.debug(f"SASL PLAIN variant 1 failed: {e}")

                    # Variant 2: authzid=username, authcid=username
                    try:
                        self._try_sasl_plain(self.username, self.username, self.password)
                        self._connected = True
                        return self.conn
                    except Exception as e:
                        logger.debug(f"SASL PLAIN variant 2 failed: {e}")

                    raise RuntimeError(
                        f"SASL PLAIN authentication failed for all variants on {self.host}:{self.port}"
                    )

                # No supported auth methods
                raise RuntimeError(
                    f"IMAP server {self.host}:{self.port} does not support LOGIN or PLAIN after STARTTLS"
                )

            else:
                # Plain LOGIN (only safe if server allows it)
                logger.debug("Using plain LOGIN authentication")
                self.conn.login(self.username, self.password)
                self._connected = True

            return self.conn

        except Exception as e:
            logger.error(f"Failed to connect to IMAP server {self.host}:{self.port}: {e}")
            self._cleanup()
            raise

    def __exit__(self, exc_type, exc, tb):
        self._cleanup()

    def _cleanup(self):
        """Clean up the connection."""
        if self.conn is not None:
            try:
                self.conn.logout()
                logger.debug(f"Logged out from {self.host}:{self.port}")
            except Exception as e:
                logger.warning(f"Error during logout from {self.host}:{self.port}: {e}")
            finally:
                self.conn = None
                self._connected = False
