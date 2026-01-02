from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import subprocess
import os
from io import BytesIO

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

@router.get("/settings/backup")
def backup_db(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    dsn = os.getenv("DB_DSN")
    if not dsn:
        flash(request, "DB_DSN not configured.")
        return RedirectResponse("/settings", status_code=303)

    try:
        result = subprocess.run(["pg_dump", dsn, "--format=plain"], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            flash(request, f"Backup failed: {result.stderr}")
            return RedirectResponse("/settings", status_code=303)

        bio = BytesIO(result.stdout.encode('utf-8'))
        return StreamingResponse(
            bio,
            media_type="application/sql",
            headers={"Content-Disposition": "attachment; filename=daygle_backup.sql"}
        )
    except Exception as e:
        flash(request, f"Backup error: {str(e)}")
        return RedirectResponse("/settings", status_code=303)

@router.post("/settings/restore")
def restore_db(request: Request, file: UploadFile = File(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    dsn = os.getenv("DB_DSN")
    if not dsn:
        flash(request, "DB_DSN not configured.")
        return RedirectResponse("/settings", status_code=303)

    try:
        content = file.file.read().decode('utf-8')
        result = subprocess.run(["psql", dsn], input=content, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            flash(request, f"Restore failed: {result.stderr}")
            return RedirectResponse("/settings", status_code=303)

        flash(request, "Database restored successfully.")
        return RedirectResponse("/settings", status_code=303)
    except Exception as e:
        flash(request, f"Restore error: {str(e)}")
        return RedirectResponse("/settings", status_code=303)