from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from utils.db import query
from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

@router.get("/logs")
def logs(request: Request, level: str = "all"):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    where = ""
    params = {}
    if level != "all":
        where = "WHERE level = :level"
        params["level"] = level

    rows = query(
        f"""
        SELECT id, timestamp, level, source, message, details
        FROM logs
        {where}
        ORDER BY timestamp DESC
        LIMIT 200
        """,
        params
    ).mappings().all()

    return templates.TemplateResponse(
        "logs.html",
        {"request": request, "errors": rows, "current_level": level},
    )