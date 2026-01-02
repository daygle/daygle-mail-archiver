from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return "user_id" in request.session

@router.get("/error_log")
def error_log(request: Request, level: str = "all"):
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
        FROM error_log
        {where}
        ORDER BY timestamp DESC
        LIMIT 200
        """,
        params
    ).mappings().all()

    return templates.TemplateResponse(
        "error_log.html",
        {"request": request, "errors": rows, "current_level": level},
    )