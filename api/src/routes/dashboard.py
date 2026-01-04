from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from datetime import datetime, timedelta

from utils.db import query

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def require_login(request: Request):
    return "user_id" in request.session


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    """Dashboard page with charts"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "flash": flash}
    )


@router.get("/api/dashboard/emails-per-day")
def emails_per_day(request: Request):
    """Get total emails per day for the last 30 days"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = query("""
        SELECT 
            DATE(created_at) as date,
            COUNT(*) as count
        FROM messages
        WHERE created_at >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(created_at)
        ORDER BY date
    """).mappings().all()

    return {
        "labels": [row["date"].strftime("%Y-%m-%d") for row in results],
        "data": [row["count"] for row in results]
    }


@router.get("/api/dashboard/top-senders")
def top_senders(request: Request):
    """Get top 10 senders"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = query("""
        SELECT 
            COALESCE(sender, 'Unknown') as sender,
            COUNT(*) as count
        FROM messages
        WHERE sender IS NOT NULL AND sender != ''
        GROUP BY sender
        ORDER BY count DESC
        LIMIT 10
    """).mappings().all()

    return {
        "labels": [row["sender"] for row in results],
        "data": [row["count"] for row in results]
    }


@router.get("/api/dashboard/top-receivers")
def top_receivers(request: Request):
    """Get top 10 receivers"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Parse recipients field and count occurrences
    # This is a simplified version - you may want to enhance this based on your recipient format
    results = query("""
        WITH recipients_parsed AS (
            SELECT 
                TRIM(unnest(string_to_array(recipients, ','))) as recipient
            FROM messages
            WHERE recipients IS NOT NULL AND recipients != ''
        )
        SELECT 
            recipient,
            COUNT(*) as count
        FROM recipients_parsed
        WHERE recipient IS NOT NULL AND recipient != ''
        GROUP BY recipient
        ORDER BY count DESC
        LIMIT 10
    """).mappings().all()

    return {
        "labels": [row["recipient"] for row in results],
        "data": [row["count"] for row in results]
    }


@router.get("/api/dashboard/total-emails")
def total_emails(request: Request):
    """Get total number of emails"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = query("SELECT COUNT(*) as count FROM messages").mappings().first()
    
    return {
        "total": results["count"] if results else 0
    }


@router.get("/api/database-size")
def database_size(request: Request):
    """Get database size"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    results = query("""
        SELECT 
            pg_size_pretty(pg_database_size(current_database())) as size,
            pg_database_size(current_database()) as size_bytes
    """).mappings().first()
    
    return {
        "size": results["size"] if results else "0 bytes",
        "size_bytes": results["size_bytes"] if results else 0
    }


@router.get("/api/dashboard/stats")
def dashboard_stats(request: Request):
    """Get all dashboard statistics in one call"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Total emails
    total_results = query("SELECT COUNT(*) as count FROM messages").mappings().first()
    total_emails = total_results["count"] if total_results else 0

    # Database size
    size_results = query("""
        SELECT 
            pg_size_pretty(pg_database_size(current_database())) as size,
            pg_database_size(current_database()) as size_bytes
    """).mappings().first()
    db_size = size_results["size"] if size_results else "0 bytes"

    # Total accounts
    accounts_results = query("SELECT COUNT(*) as count FROM imap_accounts").mappings().first()
    total_accounts = accounts_results["count"] if accounts_results else 0

    # Emails today
    today_results = query("""
        SELECT COUNT(*) as count 
        FROM messages 
        WHERE DATE(created_at) = CURRENT_DATE
    """).mappings().first()
    emails_today = today_results["count"] if today_results else 0

    return {
        "total_emails": total_emails,
        "database_size": db_size,
        "total_accounts": total_accounts,
        "emails_today": emails_today
    }
