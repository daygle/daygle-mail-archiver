from datetime import datetime, timezone
from fastapi import Request
from utils.db import query

def get_client_ip(request: Request) -> str:
    """Extract client IP address from FastAPI request, handling proxies"""
    # Check for X-Forwarded-For header (common with proxies/load balancers)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, take the first one
        return forwarded_for.split(",")[0].strip()

    # Check for X-Real-IP header (nginx)
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip

    # Fall back to request.client.host
    if request.client and request.client.host:
        return request.client.host

    return "unknown"

def log(level: str, source: str, message: str, details: str = "", ip_address: str = None):
    query(
        """
        INSERT INTO logs (timestamp, level, source, message, details, ip_address)
        VALUES (:ts, :level, :source, :message, :details, :ip_address)
        """,
        {
            "ts": datetime.now(timezone.utc),
            "level": level,
            "source": source,
            "message": message[:500],
            "details": details[:4000],
            "ip_address": ip_address,
        },
    )