import imaplib
import ssl
import base64

class ImapConnection:
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
        self.conn = None

    def _try_sasl_plain(self, authzid, authcid, password):
        """Try a SASL PLAIN authentication variant."""
        auth_string = base64.b64encode(
            f"{authzid}\0{authcid}\0{password}".encode("utf-8")
        ).decode("ascii")

        def auth_plain(_):
            return auth_string

        return self.conn.authenticate("PLAIN", auth_plain)

    def __enter__(self):
        # SSL connection
        if self.use_ssl:
            context = ssl.create_default_context()
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
            self.conn.login(self.username, self.password)
            return self.conn

        # Non-SSL connection
        self.conn = imaplib.IMAP4(self.host, self.port)

        # STARTTLS upgrade
        if self.require_starttls:
            self.conn.starttls()

            # Fetch capabilities (may contain bytes, str, or nested lists)
            caps = self.conn.capability()

            # Normalize capabilities: flatten nested lists and convert everything to bytes
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

            caps_flat = b" ".join(normalized_caps)

            # LOGIN allowed?
            if b"AUTH=LOGIN" in caps_flat:
                self.conn.login(self.username, self.password)
                return self.conn

            # SASL PLAIN allowed?
            if b"AUTH=PLAIN" in caps_flat:
                # Try SASL PLAIN variants in order

                # Variant 1: authzid="", authcid=username
                try:
                    self._try_sasl_plain("", self.username, self.password)
                    return self.conn
                except Exception:
                    pass

                # Variant 2: authzid=username, authcid=username
                try:
                    self._try_sasl_plain(self.username, self.username, self.password)
                    return self.conn
                except Exception:
                    pass

                # Variant 3: authzid="", authcid=full email (same as username here)
                try:
                    self._try_sasl_plain("", self.username, self.password)
                    return self.conn
                except Exception:
                    pass

                # Variant 4: authzid=full email, authcid=full email
                try:
                    self._try_sasl_plain(self.username, self.username, self.password)
                    return self.conn
                except Exception:
                    pass

                raise RuntimeError(
                    f"SASL PLAIN authentication failed for all variants on {self.host}:{self.port}"
                )

            # No supported auth methods
            raise RuntimeError(
                f"IMAP server {self.host}:{self.port} does not support LOGIN or PLAIN after STARTTLS"
            )

        else:
            # Plain LOGIN (only safe if server allows it)
            self.conn.login(self.username, self.password)

        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.conn is not None:
                self.conn.logout()
        except Exception:
            pass
