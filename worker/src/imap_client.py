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

            # Capability-based authentication
            caps = self.conn.capability()  # list of bytes
            caps_flat = b" ".join(caps)

            if b"AUTH=LOGIN" in caps_flat:
                # Standard LOGIN
                self.conn.login(self.username, self.password)

            elif b"AUTH=PLAIN" in caps_flat:
                # SASL PLAIN
                auth_string = base64.b64encode(
                    f"\0{self.username}\0{self.password}".encode("utf-8")
                ).decode("ascii")

                def auth_plain(_):
                    return auth_string

                self.conn.authenticate("PLAIN", auth_plain)

            else:
                raise RuntimeError(
                    f"IMAP server {self.host}:{self.port} does not advertise AUTH=LOGIN or AUTH=PLAIN after STARTTLS"
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
