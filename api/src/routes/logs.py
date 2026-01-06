from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from utils.db import query
from utils.templates import templates

router = APIRouter()

ALLOWED_LOG_LEVELS = ["all", "info", "warning", "error"]

def require_login(request: Request):
    return "user_id" in request.session

@router.get("/logs")
def logs(
    request: Request, 
    level: str = "all", 
    page: int = 1,
    search: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = ""
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)
    
    # Validate log level
    if level not in ALLOWED_LOG_LEVELS:
        level = "all"
    
    # Get page_size from user settings, fallback to global settings
    user_id = request.session.get("user_id")
    page_size = 50  # Default
    
    if user_id:
        user_result = query("SELECT page_size FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user_result and user_result["page_size"]:
            page_size = user_result["page_size"]
    
    if not user_id or not page_size:
        global_result = query("SELECT value FROM settings WHERE key = 'page_size'").mappings().first()
        if global_result:
            page_size = int(global_result["value"])
    
    # Validate pagination parameters
    page = max(1, page)
    page_size = min(max(10, page_size), 500)  # Between 10 and 500
    offset = (page - 1) * page_size
    
    # Build WHERE clause with filters
    where_conditions = []
    params = {"limit": page_size, "offset": offset}
    
    if level != "all":
        where_conditions.append("level = :level")
        params["level"] = level
    
    if search:
        where_conditions.append("(message ILIKE :search OR source ILIKE :search)")
        params["search"] = f"%{search}%"
    
    if source:
        where_conditions.append("source = :source")
        params["source"] = source
    
    if date_from:
        try:
            # Parse date and add to conditions
            where_conditions.append("timestamp >= :date_from")
            params["date_from"] = date_from
        except ValueError:
            pass  # Invalid date format, skip filter
    
    if date_to:
        try:
            # Parse date and add to conditions (include full day)
            where_conditions.append("timestamp < :date_to::date + interval '1 day'")
            params["date_to"] = date_to
        except ValueError:
            pass  # Invalid date format, skip filter
    
    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)
    
    # Get total count for pagination
    count_query = f"SELECT COUNT(*) as total FROM logs {where_clause}"
    total_result = query(count_query, params).mappings().first()
    total_logs = total_result["total"] if total_result else 0
    total_pages = (total_logs + page_size - 1) // page_size  # Ceiling division
    
    # Get paginated logs
    logs_query = f"""
        SELECT id, timestamp, level, source, message, details
        FROM logs
        {where_clause}
        ORDER BY timestamp DESC
        LIMIT :limit OFFSET :offset
    """
    
    rows = query(logs_query, params).mappings().all()
    
    # Get distinct sources for filter dropdown
    sources_query = "SELECT DISTINCT source FROM logs ORDER BY source"
    sources = [row["source"] for row in query(sources_query).mappings().all()]
    
    # Check if any filters are active
    has_active_filters = bool(search or source or (level != "all") or date_from or date_to)

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "logs": rows,
            "current_level": level,
            "current_search": search,
            "current_source": source,
            "current_date_from": date_from,
            "current_date_to": date_to,
            "page": page,
            "page_size": page_size,
            "total_logs": total_logs,
            "total_pages": total_pages,
            "allowed_levels": ALLOWED_LOG_LEVELS,
            "sources": sources,
            "has_active_filters": has_active_filters
        },
    )