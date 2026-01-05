from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse
import subprocess
import os
from io import BytesIO
from urllib.parse import urlparse

from utils.db import query, execute
from utils.logger import log
from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def flash(request: Request, message: str):
    request.session["flash"] = message

@router.get("/global-settings")
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

@router.post("/global-settings")
def save_settings(
    request: Request,
    page_size: int = Form(...),
    date_format: str = Form("%d/%m/%Y"),
    time_format: str = Form("%H:%M"),
    timezone: str = Form("Australia/Melbourne"),
    enable_purge: bool = Form(False),
    retention_value: int = Form(1),
    retention_unit: str = Form("years"),
    retention_delete_from_mail_server: bool = Form(False),
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    try:
        # Batch update all settings in a single query for better performance
        settings_data = [
            ('page_size', str(page_size)),
            ('date_format', date_format),
            ('time_format', time_format),
            ('timezone', timezone),
            ('enable_purge', str(enable_purge).lower()),
            ('retention_value', str(retention_value)),
            ('retention_unit', retention_unit),
            ('retention_delete_from_mail_server', str(retention_delete_from_mail_server).lower()),
        ]
        
        for key, value in settings_data:
            execute(
                """
                INSERT INTO settings (key, value)
                VALUES (:key, :value)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """,
                {"key": key, "value": value},
            )
        
        # Update session variables
        request.session["date_format"] = date_format
        request.session["time_format"] = time_format
        request.session["timezone"] = timezone
    except Exception as e:
        log("error", "Settings", f"Failed to save settings: {str(e)}", "")
        flash(request, f"Failed to save settings: {str(e)}")
        return RedirectResponse("/global-settings", status_code=303)

    username = request.session.get("username", "unknown")
    log("info", "Settings", f"User '{username}' updated global settings (page_size={page_size}, date_format={date_format}, time_format={time_format}, timezone={timezone}, enable_purge={enable_purge}, retention={retention_value} {retention_unit}, delete_from_mail_server={retention_delete_from_mail_server})", "")

    flash(request, "Settings updated successfully.")
    return RedirectResponse("/global-settings", status_code=303)
@router.get("/backup")
def backup_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("backup_restore.html", {"request": request, "flash": msg})

@router.get("/restore")
def restore_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    msg = request.session.pop("flash", None)
    return templates.TemplateResponse("backup_restore.html", {"request": request, "flash": msg})
@router.get("/global-settings/backup")
def backup_db(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    dsn = os.getenv("DB_DSN")
    if not dsn:
        flash(request, "DB_DSN not configured.")
        return RedirectResponse("/global-settings", status_code=303)

    try:
        parsed = urlparse(dsn)
        host = parsed.hostname
        port = parsed.port or 5432
        user = parsed.username
        password = parsed.password
        dbname = parsed.path.lstrip('/')
        
        if not all([host, user, password, dbname]):
            flash(request, "Invalid database configuration.")
            return RedirectResponse("/global-settings", status_code=303)

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

        username = request.session.get("username", "unknown")
        log("info", "Database", f"User '{username}' performed database backup", "")

        bio = BytesIO(result.stdout.encode('utf-8'))
        return StreamingResponse(
            bio,
            media_type="application/sql",
            headers={"Content-Disposition": "attachment; filename=daygle_backup.sql"}
        )
    except subprocess.TimeoutExpired:
        flash(request, "Backup timed out after 60 seconds. The database may be too large.")
        return RedirectResponse("/backup", status_code=303)
    except Exception as e:
        log("error", "Database", f"Backup failed: {str(e)}", "")
        flash(request, f"Backup error: {str(e)}")
        return RedirectResponse("/backup", status_code=303)

@router.post("/global-settings/restore")
def restore_db(request: Request, file: UploadFile = File(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Validate file extension
    if not file.filename.endswith(('.sql', '.txt')):
        flash(request, "Invalid file type. Please upload a .sql file.")
        return RedirectResponse("/restore", status_code=303)

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

        # Limit file size to prevent memory issues (10MB max)
        content_bytes = file.file.read()
        if len(content_bytes) > 10 * 1024 * 1024:
            flash(request, "File too large. Maximum size is 10MB.")
            return RedirectResponse("/restore", status_code=303)
        
        content = content_bytes.decode('utf-8')

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

        username = request.session.get("username", "unknown")
        log("warning", "Database", f"User '{username}' performed database restore from file '{file.filename}'", "")

        flash(request, "Database restored successfully.")
        return RedirectResponse("/restore", status_code=303)
    except subprocess.TimeoutExpired:
        flash(request, "Restore timed out after 120 seconds. The file may be too large.")
        return RedirectResponse("/restore", status_code=303)
    except UnicodeDecodeError:
        flash(request, "Invalid file encoding. Please ensure the file is valid SQL in UTF-8 format.")
        return RedirectResponse("/restore", status_code=303)
    except Exception as e:
        log("error", "Database", f"Restore failed: {str(e)}", "")
        flash(request, f"Restore error: {str(e)}")
        return RedirectResponse("/restore", status_code=303)