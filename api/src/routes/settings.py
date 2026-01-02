from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from utils.db import query
from utils.settings import (
    get_retention_config,
    set_retention_config,
    set_retention_last_run,
)
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def require_login(request: Request):
    return request.session.get("user") is not None


def flash(request: Request, message: str):
    request.session["flash"] = message


def compute_retention_cutoff(retention):
    """Return a datetime cutoff based on retention settings, or None."""
    enabled = retention["enabled"] == "true"
    if not enabled:
        return None

    try:
        value = int(retention["value"])
    except ValueError:
        return None

    if value < 1:
        return None

    unit = retention["unit"]
    now = datetime.utcnow()

    if unit == "days":
        return now - timedelta(days=value)
    elif unit == "months":
        return now - relativedelta(months=value)
    elif unit == "years":
        return now - relativedelta(years=value)
    else:
        return None


# ---------------------------------------------------------
# Unified Settings Page (Page Size + Retention)
# ---------------------------------------------------------

@router.get("/settings")
def settings_form(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Load general settings
    rows = query("SELECT key, value FROM settings").mappings().all()
    settings = {r["key"]: r["value"] for r in rows}

    # Load retention settings
    retention = get_retention_config()

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "retention": retention,
            "flash": msg,
        },
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

    flash(request, "Settings saved successfully.")
    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------
# Retention Settings (Integrated into /settings)
# ---------------------------------------------------------

@router.post("/settings/retention")
def update_retention_settings(
    request: Request,
    retention_enabled: str | None = Form(None),
    retention_value: int = Form(...),
    retention_unit: str = Form(...),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    enabled = retention_enabled is not None

    if retention_value < 1:
        flash(request, "Retention value must be at least 1.")
        return RedirectResponse("/settings", status_code=303)

    if retention_unit not in ("days", "months", "years"):
        flash(request, "Invalid retention unit.")
        return RedirectResponse("/settings", status_code=303)

    set_retention_config(enabled, retention_value, retention_unit)
    flash(request, "Retention policy updated successfully.")

    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------
# Retention Preview
# ---------------------------------------------------------

@router.post("/settings/retention/preview")
def preview_retention_purge(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    retention = get_retention_config()
    cutoff = compute_retention_cutoff(retention)

    if cutoff is None:
        flash(request, "Retention policy disabled or invalid.")
        return RedirectResponse("/settings", status_code=303)

    row = query(
        """
        SELECT COUNT(*) AS c
        FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    ).mappings().first()

    count = row["c"] if row else 0
    flash(request, f"{count} message(s) would be purged.")

    return RedirectResponse("/settings", status_code=303)


# ---------------------------------------------------------
# Retention Purge Now
# ---------------------------------------------------------

@router.post("/settings/retention/purge")
def run_retention_purge_now(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    retention = get_retention_config()
    cutoff = compute_retention_cutoff(retention)

    if cutoff is None:
        flash(request, "Retention policy disabled or invalid.")
        return RedirectResponse("/settings", status_code=303)

    row = query(
        """
        SELECT COUNT(*) AS c
        FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    ).mappings().first()

    count = row["c"] if row else 0

    query(
        """
        DELETE FROM messages
        WHERE created_at < :cutoff
        """,
        {"cutoff": cutoff},
    )

    set_retention_last_run(datetime.utcnow())
    flash(request, f"Purged {count} message(s).")

    return RedirectResponse("/settings", status_code=303)
