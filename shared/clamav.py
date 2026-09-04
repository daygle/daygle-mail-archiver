"""Pure ClamAV helpers shared between the API and worker scanners.

Only logic that is byte-for-byte identical (and has no database, logging, or
alerting dependencies) lives here. The operational scanner (worker) and the
simplified import scanner (API) intentionally keep their own settings loading,
connection bookkeeping, and alerting - those genuinely differ.
"""

# Maximum email size to scan (100MB) - very large emails are skipped
MAX_SCAN_SIZE_DEFAULT = 100 * 1024 * 1024


def scan_result_details(result):
    """Return the status/name pair from pyclamd's scan_stream response.

    pyclamd returns ``{"stream": ("FOUND", "virus_name")}`` for a detection;
    older versions/tests may return the tuple (or list) directly.
    """
    if isinstance(result, dict):
        result = next(iter(result.values()), None)
    if isinstance(result, (tuple, list)):
        status = result[0] if result else None
        name = result[1] if len(result) > 1 else None
        return status, name
    return None, None
