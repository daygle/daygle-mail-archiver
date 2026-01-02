from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return request.session.get("user") is not None

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/settings")
def settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    rows = query("SELECT key, value FROM settings").mappings().all()
    settings = {r["key"]: r["value"] for r in rows}

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "settings": settings, "flash": msg},
    )

@router.post("/settings")
def save_settings(
    request: Request,
    page_size: int = Form(...),
    enable_purge: bool = Form(False),
    retention_value: int = Form(1),
    retention_unit: str = Form("years"),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Save page_size
    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('page_size', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": str(page_size)},
    )

    # Save retention settings
    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('enable_purge', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": str(enable_purge).lower()},
    )
    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('retention_value', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": str(retention_value)},
    )
    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('retention_unit', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": retention_unit},
    )

    flash(request, "Settings updated successfully.")
    return RedirectResponse("/settings", status_code=303)