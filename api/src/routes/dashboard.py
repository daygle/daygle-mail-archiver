from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from collections import defaultdict

from utils.db import query
from utils.logger import log
from utils.templates import templates

router = APIRouter()


def require_login(request: Request):
    return "user_id" in request.session


def get_user_date_format(request: Request, date_only: bool = False) -> str:
    """Get the user's preferred date format, falling back to global setting"""
    # Get global date format
    try:
        global_setting = query("SELECT value FROM settings WHERE key = 'date_format'").mappings().first()
        date_format = global_setting["value"] if global_setting else "%d/%m/%Y %H:%M"
    except Exception:
        date_format = "%d/%m/%Y %H:%M"
    
    # Override with user's date format if set
    user_id = request.session.get("user_id")
    if user_id:
        try:
            user = query("SELECT date_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            if user and user["date_format"]:
                date_format = user["date_format"]
        except Exception:
            pass
    
    # Extract just date portion if requested
    if date_only and (" %H" in date_format or " %I" in date_format):
        date_format = date_format.split()[0]
    
    return date_format


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

    try:
        date_format = get_user_date_format(request, date_only=True)

        results = query("""
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as count
            FROM emails
            WHERE created_at >= NOW() - INTERVAL '30 days'
            GROUP BY DATE(created_at)
            ORDER BY date
        """).mappings().all()

        return {
            "labels": [row["date"].strftime(date_format) for row in results],
            "data": [row["count"] for row in results]
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch emails per day for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/top-senders")
def top_senders(request: Request):
    """Get top 10 senders"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        results = query("""
            SELECT 
                COALESCE(sender, 'Unknown') as sender,
                COUNT(*) as count
            FROM emails
            WHERE sender IS NOT NULL AND sender != ''
            GROUP BY sender
            ORDER BY count DESC
            LIMIT 10
        """).mappings().all()

        return {
            "labels": [row["sender"] for row in results],
            "data": [row["count"] for row in results]
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch top senders for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/top-receivers")
def top_receivers(request: Request):
    """Get top 10 receivers"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Parse recipients field and count occurrences
        results = query("""
            WITH recipients_parsed AS (
                SELECT 
                    TRIM(unnest(string_to_array(recipients, ','))) as recipient
                FROM emails
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
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch top receivers for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/total-emails")
def total_emails(request: Request):
    """Get total number of emails"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        results = query("SELECT COUNT(*) as count FROM emails").mappings().first()
        
        return {
            "total": results["count"] if results else 0
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch total emails for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/database-size")
def database_size(request: Request):
    """Get database size"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        results = query("""
            SELECT 
                pg_size_pretty(pg_database_size(current_database())) as size,
                pg_database_size(current_database()) as size_bytes
        """).mappings().first()
        
        return {
            "size": results["size"] if results else "0 bytes",
            "size_bytes": results["size_bytes"] if results else 0
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch database size for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/stats")
def dashboard_stats(request: Request):
    """Get all dashboard statistics in one call"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Total emails
        total_results = query("SELECT COUNT(*) as count FROM emails").mappings().first()
        total_emails_count = total_results["count"] if total_results else 0

        # Database size
        size_results = query("""
            SELECT 
                pg_size_pretty(pg_database_size(current_database())) as size,
                pg_database_size(current_database()) as size_bytes
        """).mappings().first()
        db_size = size_results["size"] if size_results else "0 bytes"

        # Total accounts
        accounts_results = query("SELECT COUNT(*) as count FROM fetch_accounts").mappings().first()
        total_accounts = accounts_results["count"] if accounts_results else 0

        # Emails today
        today_results = query("""
            SELECT COUNT(*) as count 
            FROM emails 
            WHERE DATE(created_at) = CURRENT_DATE
        """).mappings().first()
        emails_today = today_results["count"] if today_results else 0

        return {
            "total_emails": total_emails_count,
            "database_size": db_size,
            "total_accounts": total_accounts,
            "emails_today": emails_today
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch dashboard stats for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/dashboard/deletion-stats")
def deletion_stats(request: Request):
    """Get deletion statistics for the last 30 days"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        date_format = get_user_date_format(request, date_only=True)

        # Get deletion stats grouped by type
        results = query("""
            SELECT 
                deletion_date,
                deletion_type,
                deleted_from_mail_server,
                SUM(count) as total_count
            FROM deletion_stats
            WHERE deletion_date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY deletion_date, deletion_type, deleted_from_mail_server
            ORDER BY deletion_date
        """).mappings().all()

        # Organize data for chart
        data = {
            "labels": [],
            "manual": [],
            "retention": [],
            "deleted_from_server": []
        }
        
        # Group by date
        by_date = defaultdict(lambda: {"manual": 0, "retention": 0, "from_server": 0})
        
        for row in results:
            date_str = row["deletion_date"].strftime(date_format)
            if row["deletion_type"] == "manual":
                by_date[date_str]["manual"] += row["total_count"]
            elif row["deletion_type"] == "retention":
                by_date[date_str]["retention"] += row["total_count"]
            
            if row["deleted_from_mail_server"]:
                by_date[date_str]["from_server"] += row["total_count"]
        
        # Sort by date and build arrays
        sorted_dates = sorted(by_date.keys())
        for date_str in sorted_dates:
            data["labels"].append(date_str)
            data["manual"].append(by_date[date_str]["manual"])
            data["retention"].append(by_date[date_str]["retention"])
            data["deleted_from_server"].append(by_date[date_str]["from_server"])
        
        return data
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Dashboard", f"Failed to fetch deletion stats for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)
