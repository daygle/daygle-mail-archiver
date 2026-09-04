from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from ..utils.db import query
from ..utils.templates import templates
from ..utils.timezone import format_datetime, get_display_prefs
from ..utils.permissions import require_permission, PERMISSIONS

router = APIRouter()

ALLOWED_LOG_LEVELS = ["all", "debug", "info", "warning", "error", "success"]
DEFAULT_PAGE_SIZE = 50
MIN_PAGE_SIZE = 10
MAX_PAGE_SIZE = 500
MAX_FILTER_LENGTH = 200


def require_login(request: Request):
    return "user_id" in request.session


@router.get("/logs")
def logs(
    request: Request,
    _=require_permission(PERMISSIONS["view_logs"]),
    level: str = "all",
    page: int = 1,
    search: str = "",
    source: str = "",
    date_from: str = "",
    date_to: str = "",
):
    # Require login
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Normalize bounded user input before using it in filters or pagination.
    level = (level or "all").strip().lower()
    if level not in ALLOWED_LOG_LEVELS:
        level = "all"
    search = (search or "").strip()[:MAX_FILTER_LENGTH]
    source = (source or "").strip()[:MAX_FILTER_LENGTH]

    def _parse_date(value: str):
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            return None

    parsed_date_from = _parse_date(date_from)
    parsed_date_to = _parse_date(date_to)
    date_from = parsed_date_from.isoformat() if parsed_date_from else ""
    date_to = parsed_date_to.isoformat() if parsed_date_to else ""

    def _coerce_page_size(value):
        try:
            return min(max(MIN_PAGE_SIZE, int(value)), MAX_PAGE_SIZE)
        except (TypeError, ValueError):
            return None

    # Get page_size from user settings, falling back to the global setting and
    # finally a safe constant if either value is missing or malformed.
    user_id = request.session.get("user_id")
    page_size = None
    if user_id:
        try:
            user_result = query(
                "SELECT page_size FROM users WHERE id = :id", {"id": user_id}
            ).mappings().first()
            if user_result:
                page_size = _coerce_page_size(user_result.get("page_size"))
        except Exception:
            page_size = None

    if page_size is None:
        try:
            global_result = query(
                "SELECT value FROM settings WHERE key = 'page_size'"
            ).mappings().first()
            if global_result:
                page_size = _coerce_page_size(global_result.get("value"))
        except Exception:
            page_size = None

    page_size = page_size or DEFAULT_PAGE_SIZE
    page = max(1, page)
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

    if parsed_date_from:
        where_conditions.append("timestamp >= :date_from")
        params["date_from"] = parsed_date_from

    if parsed_date_to:
        # Include the complete selected end date while keeping the value typed.
        where_conditions.append("timestamp < CAST(:date_to AS DATE) + interval '1 day'")
        params["date_to"] = parsed_date_to

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)

    # Get total count for pagination
    count_query = f"SELECT COUNT(*) as total FROM logs {where_clause}"
    total_result = query(count_query, params).mappings().first()
    total_logs = total_result["total"] if total_result else 0
    total_pages = (total_logs + page_size - 1) // page_size  # Ceiling division
    # Avoid needlessly large OFFSET values and keep the displayed page valid.
    if total_pages:
        page = min(page, total_pages)
    else:
        page = 1
    params["offset"] = (page - 1) * page_size

    # Get paginated logs
    logs_query = f"""
        SELECT id, timestamp, level, source, message, details
        FROM logs
        {where_clause}
        ORDER BY timestamp DESC, id DESC
        LIMIT :limit OFFSET :offset
    """

    rows = query(logs_query, params).mappings().all()

    # Normalize rows to dicts and expose source_label (use DB value directly).
    # Resolve display preferences once and pre-format each entry's timestamp so
    # a 50-entry page does not issue a timezone/format lookup per row (the
    # template renders timestamp_formatted instead of re-formatting per row).
    rows = [dict(r) for r in rows]
    _tz, _date_format, _time_format = get_display_prefs(user_id)
    for r in rows:
        r["source_label"] = r.get("source")
        r["timestamp_formatted"] = (
            format_datetime(r["timestamp"], user_id, date_format=_date_format,
                            time_format=_time_format, tz=_tz)
            if r.get("timestamp") else None
        )

    # Get distinct sources for filter dropdown
    sources_query = "SELECT DISTINCT source FROM logs WHERE source IS NOT NULL AND source <> '' ORDER BY source"
    sources_values = [row["source"] for row in query(sources_query).mappings().all()]
    sources = [{"value": s, "label": s} for s in sources_values]

    # Check if any filters are active
    has_active_filters = bool(
        search or source or (level != "all") or date_from or date_to
    )

    # Archive-wide stats for the stat cards. Defensive: a stats failure must
    # never take the page down with it.
    stats = {"total": 0, "debug": 0, "info": 0, "warning": 0, "error": 0, "success": 0}
    try:
        stats_rows = query(
            "SELECT level, COUNT(*) AS c FROM logs GROUP BY level"
        ).mappings().all()
        for r in stats_rows:
            log_level = r.get("level") or "debug"
            if log_level in stats:
                stats[log_level] = r.get("c", 0) or 0
        stats["total"] = sum(stats.values())
    except Exception:
        pass

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
            "has_active_filters": has_active_filters,
            "stats": stats,
        },
    )
    