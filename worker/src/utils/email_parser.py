"""Email parser utilities for worker."""
import hashlib


def compute_signature(raw: bytes) -> str:
    """Compute SHA256 hex signature of the raw email bytes."""
    if raw is None:
        return ""
    h = hashlib.sha256()
    h.update(raw)
    return h.hexdigest()
