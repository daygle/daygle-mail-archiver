from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import subprocess
import os
from io import BytesIO
from urllib.parse import urlparse

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")

def require_login(request: Request):
    return "user_id" in request.session

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
        "global_settings.html",
        {"request": request, "settings": settings, "flash": msg},
    )

@router.post("/settings")
def save_settings(
    request: Request,
    page_size: int = Form(...),
    date_format: str = Form("%d/%m/%Y %H:%M"),
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

    # Save date_format to session and database
    request.session["date_format"] = date_format
    query(
        """
        INSERT INTO settings (key, value)
        VALUES ('date_format', :v)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        {"v": date_format},
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
@router.get("/backup")
def backup_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("backup.html", {"request": request, "flash": msg})

@router.get("/restore")
def restore_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("restore.html", {"request": request, "flash": msg})
@router.get("/settings/backup")
def backup_db(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    dsn = os.getenv("DB_DSN")
    if not dsn:
        flash(request, "DB_DSN not configured.")
        return RedirectResponse("/settings", status_code=303)

    try:
        parsed = urlparse(dsn)
        host = parsed.hostname
        port = parsed.port or 5432
        user = parsed.username
        password = parsed.password
        dbname = parsed.path.lstrip('/')

        env = os.environ.copy()
        env['PGPASSWORD'] = password

        result = subprocess.run([
            "pg_dump",
            "--host", host,
            "--port", str(port),
            "--username", user,
            "--dbname", dbname,
            "--format=plain"
        ], env=env, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            flash(request, f"Backup failed: {result.stderr}")
            return RedirectResponse("/backup", status_code=303)

        bio = BytesIO(result.stdout.encode('utf-8'))
        return StreamingResponse(
            bio,
            media_type="application/sql",
            headers={"Content-Disposition": "attachment; filename=daygle_backup.sql"}
        )
    except Exception as e:
        flash(request, f"Backup error: {str(e)}")
        return RedirectResponse("/backup", status_code=303)

@router.post("/settings/restore")
def restore_db(request: Request, file: UploadFile = File(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    dsn = os.getenv("DB_DSN")
    if not dsn:
        flash(request, "DB_DSN not configured.")
        return RedirectResponse("/restore", status_code=303)

    try:
        parsed = urlparse(dsn)
        host = parsed.hostname
        port = parsed.port or 5432
        user = parsed.username
        password = parsed.password
        dbname = parsed.path.lstrip('/')

        content = file.file.read().decode('utf-8')

        env = os.environ.copy()
        env['PGPASSWORD'] = password

        result = subprocess.run([
            "psql",
            "--host", host,
            "--port", str(port),
            "--username", user,
            "--dbname", dbname
        ], env=env, input=content, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            flash(request, f"Restore failed: {result.stderr}")
            return RedirectResponse("/restore", status_code=303)

        flash(request, "Database restored successfully.")
        return RedirectResponse("/restore", status_code=303)
    except Exception as e:
        flash(request, f"Restore error: {str(e)}")
        return RedirectResponse("/restore", status_code=303)