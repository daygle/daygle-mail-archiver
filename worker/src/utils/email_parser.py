"""Email parser utilities for worker."""
import hashlib
import email.header


def compute_signature(raw: bytes) -> str:
    """Compute SHA256 hex signature of the raw email bytes."""
    if raw is None:
        return ""
    h = hashlib.sha256()
    h.update(raw)
    return h.hexdigest()


def decode_header(value) -> str:
    """Decode an email header value to a plain string.

    Handles RFC 2047 encoded-word sequences (e.g. ``=?utf-8?q?...?=``) and
    ``email.header.Header`` objects that psycopg2 cannot adapt directly.
    Returns an empty string when *value* is ``None``.
    """
    if value is None:
        return ""
    parts = email.header.decode_header(str(value))
    decoded = []
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            decoded.append(fragment.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(fragment)
    return "".join(decoded)
