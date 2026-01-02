import imaplib
import ssl

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
        if self.use_ssl:
            context = ssl.create_default_context()
            self.conn = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
        else:
            self.conn = imaplib.IMAP4(self.host, self.port)

        if self.require_starttls and not self.use_ssl:
            self.conn.starttls()

        self.conn.login(self.username, self.password)
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.conn is not None:
                self.conn.logout()
        except Exception:
            pass