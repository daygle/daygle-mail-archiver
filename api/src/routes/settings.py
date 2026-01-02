from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return request.session.get("user") is not None

@router.get("/settings")
def settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    rows = query("SELECT key, value FROM settings").mappings().all()
    settings = {r["key"]: r["value"] for r in rows}

    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings},
    )

@router.post("/settings")
def save_settings(request: Request, page_size: int = Form(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('page_size', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": str(page_size)},
    )

    return RedirectResponse("/settings", status_code=303)