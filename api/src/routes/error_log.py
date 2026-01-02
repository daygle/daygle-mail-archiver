from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return request.session.get("user") is not None

@router.get("/error_log")
def error_log(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    rows = query(
        """
        SELECT id, timestamp, source, message, details
        FROM error_log
        ORDER BY timestamp DESC
        LIMIT 200
        """
    ).mappings().all()

    return templates.TemplateResponse(
        "error_log.html",
        {"request": request, "errors": rows},
    )