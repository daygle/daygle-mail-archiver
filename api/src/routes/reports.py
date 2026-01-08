from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from collections import defaultdict
from typing import List, Dict, Any
from datetime import datetime, timedelta
import calendar

from utils.db import query
from utils.logger import log
from utils.templates import templates
from utils.timezone import convert_utc_to_user_timezone, get_user_timezone

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

def get_user_date_format(request: Request, date_only: bool = False) -> str:
    """Get the user's preferred date format, falling back to global setting"""
    # Get global date format
    try:
        global_setting = query("SELECT value FROM settings WHERE key = 'date_format'").mappings().first()
        date_format = global_setting["value"] if global_setting else "%d/%m/%Y"
    except Exception:
        date_format = "%d/%m/%Y"

    # Override with user's date format if set
    user_id = request.session.get("user_id")
    if user_id:
        try:
            user = query("SELECT date_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            if user and user["date_format"]:
                date_format = user["date_format"]
        except Exception:
            pass

    # If we only need the date part, return just date_format
    if date_only:
        return date_format

    # Get time format
    try:
        time_setting = query("SELECT value FROM settings WHERE key = 'time_format'").mappings().first()
        time_format = time_setting["value"] if time_setting else "%H:%M"
    except Exception:
        time_format = "%H:%M"

    if user_id:
        try:
            user = query("SELECT time_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
            if user and user["time_format"]:
                time_format = user["time_format"]
        except Exception:
            pass

    return f"{date_format} {time_format}"

@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    """Reports page"""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    flash = request.session.pop("flash", None)
    return templates.TemplateResponse(
        "reports.html",
        {"request": request, "flash": flash}
    )

@router.get("/api/reports/email-volume")
def email_volume_report(request: Request, start_date: str = None, end_date: str = None):
    """Get email volume report data"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        date_format = get_user_date_format(request, date_only=True)

        # Calculate period based on date range
        days_diff = (end_dt - start_dt).days
        if days_diff <= 7:
            period = "daily"
            group_by = "DATE(created_at)"
        elif days_diff <= 90:
            period = "weekly"
            group_by = "DATE_TRUNC('week', created_at)"
        else:
            period = "monthly"
            group_by = "DATE_TRUNC('month', created_at)"

        results = query(f"""
            SELECT
                {group_by} as period_start,
                COUNT(*) as email_count,
                COUNT(CASE WHEN virus_detected THEN 1 END) as virus_count,
                COUNT(DISTINCT source) as sources_count
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
            GROUP BY {group_by}
            ORDER BY period_start
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().all()

        labels = []
        email_counts = []
        virus_counts = []
        sources_counts = []

        for row in results:
            if row["period_start"]:
                local_dt = convert_utc_to_user_timezone(row["period_start"], user_id)
                if period == "daily":
                    labels.append(local_dt.strftime(date_format))
                elif period == "weekly":
                    week_end = local_dt + timedelta(days=6)
                    labels.append(f"{local_dt.strftime(date_format)} - {week_end.strftime(date_format)}")
                elif period == "monthly":
                    labels.append(local_dt.strftime("%B %Y"))

            email_counts.append(row["email_count"])
            virus_counts.append(row["virus_count"])
            sources_counts.append(row["sources_count"])

        return {
            "labels": labels,
            "email_counts": email_counts,
            "virus_counts": virus_counts,
            "sources_counts": sources_counts
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch email volume report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)

@router.get("/api/reports/account-activity")
def account_activity_report(request: Request, start_date: str = None, end_date: str = None):
    """Get account activity report data"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        date_format = get_user_date_format(request, date_only=True)

        # Get account sync data
        results = query("""
            SELECT
                fa.name,
                fa.account_type,
                fa.enabled,
                fa.last_success,
                fa.last_error,
                EXTRACT(EPOCH FROM (NOW() - fa.last_heartbeat)) / 3600 as hours_since_heartbeat,
                COUNT(e.id) as emails_synced_today
            FROM fetch_accounts fa
            LEFT JOIN emails e ON e.source = fa.name AND DATE(e.created_at) = CURRENT_DATE
            GROUP BY fa.id, fa.name, fa.account_type, fa.enabled, fa.last_success, fa.last_error, fa.last_heartbeat
            ORDER BY fa.name
        """).mappings().all()

        accounts = []
        for row in results:
            last_success = None
            if row["last_success"]:
                last_success = convert_utc_to_user_timezone(row["last_success"], user_id).strftime(get_user_date_format(request))

            accounts.append({
                "name": row["name"],
                "type": row["account_type"],
                "enabled": row["enabled"],
                "last_success": last_success,
                "last_error": row["last_error"],
                "hours_since_heartbeat": round(row["hours_since_heartbeat"] or 0, 1),
                "emails_today": row["emails_synced_today"]
            })

        # Get sync trends over time
        trend_results = query("""
            SELECT
                DATE(created_at) as sync_date,
                source,
                COUNT(*) as email_count
            FROM emails
            WHERE created_at >= NOW() - make_interval(days => :days)
            GROUP BY DATE(created_at), source
            ORDER BY sync_date, source
        """, {"days": days}).mappings().all()

        # Organize trend data
        sources = set()
        trend_data = defaultdict(lambda: defaultdict(int))

        for row in trend_results:
            date_str = convert_utc_to_user_timezone(row["sync_date"], user_id).strftime(date_format)
            source = row["source"]
            sources.add(source)
            trend_data[date_str][source] = row["email_count"]

        # Build chart data
        sorted_dates = sorted(trend_data.keys())
        chart_labels = sorted_dates
        chart_datasets = []

        for source in sorted(sources):
            data = [trend_data[date].get(source, 0) for date in sorted_dates]
            chart_datasets.append({
                "label": source,
                "data": data
            })

        return {
            "accounts": accounts,
            "trend_labels": chart_labels,
            "trend_datasets": chart_datasets
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch account activity report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)

@router.get("/api/reports/user-activity")
def user_activity_report(request: Request, days: int = 30):
    """Get user activity report data"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Only administrators can view user activity
    if request.session.get("role") != "administrator":
        return JSONResponse({"error": "Access denied"}, status_code=403)

    try:
        if days not in [7, 14, 30, 60, 90]:
            days = 30

        user_id = request.session.get("user_id")
        date_format = get_user_date_format(request, date_only=True)
        datetime_format = get_user_date_format(request)

        # User login activity
        login_results = query("""
            SELECT
                u.username,
                u.role,
                u.last_login,
                u.created_at,
                COUNT(l.id) as login_count_today
            FROM users u
            LEFT JOIN logs l ON l.source = 'Auth' AND l.message LIKE '%' || u.username || '%' AND DATE(l.timestamp) = CURRENT_DATE
            GROUP BY u.id, u.username, u.role, u.last_login, u.created_at
            ORDER BY u.username
        """).mappings().all()

        users = []
        for row in login_results:
            last_login = None
            if row["last_login"]:
                last_login = convert_utc_to_user_timezone(row["last_login"], user_id).strftime(get_user_date_format(request))

            created_at = None
            if row["created_at"]:
                created_at = convert_utc_to_user_timezone(row["created_at"], user_id).strftime(datetime_format)

            users.append({
                "username": row["username"],
                "role": row["role"],
                "last_login": last_login,
                "created_at": created_at,
                "logins_today": row["login_count_today"]
            })

        # User creation trends
        creation_results = query("""
            SELECT
                DATE(created_at) as creation_date,
                COUNT(*) as user_count
            FROM users
            WHERE created_at >= NOW() - make_interval(days => :days)
            GROUP BY DATE(created_at)
            ORDER BY creation_date
        """, {"days": days}).mappings().all()

        creation_labels = []
        creation_counts = []

        for row in creation_results:
            if row["creation_date"]:
                local_dt = convert_utc_to_user_timezone(row["creation_date"], user_id)
                creation_labels.append(local_dt.strftime(date_format))
            creation_counts.append(row["user_count"])

        return {
            "users": users,
            "creation_labels": creation_labels,
            "creation_counts": creation_counts
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch user activity report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)

@router.get("/api/reports/system-health")
def system_health_report(request: Request, start_date: str = None, end_date: str = None):
    """Get system health report data"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        date_format = get_user_date_format(request, date_only=True)

        # Database growth over time (simplified - would need historical data for accurate growth)
        db_size_results = query("""
            SELECT
                pg_size_pretty(pg_database_size(current_database())) as current_size,
                pg_database_size(current_database()) as current_size_bytes
        """).mappings().first()

        # Error trends
        error_results = query("""
            SELECT
                DATE(timestamp) as error_date,
                COUNT(*) as error_count
            FROM logs
            WHERE level = 'error' AND timestamp >= :start_date AND timestamp <= :end_date
            GROUP BY DATE(timestamp)
            ORDER BY error_date
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().all()

        error_labels = []
        error_counts = []

        for row in error_results:
            if row["error_date"]:
                local_dt = convert_utc_to_user_timezone(row["error_date"], user_id)
                error_labels.append(local_dt.strftime(date_format))
            error_counts.append(row["error_count"])

        # Worker status summary
        worker_results = query("""
            SELECT
                COUNT(*) as total_accounts,
                COUNT(CASE WHEN enabled THEN 1 END) as enabled_accounts,
                COUNT(CASE WHEN last_error IS NOT NULL THEN 1 END) as accounts_with_errors,
                AVG(EXTRACT(EPOCH FROM (NOW() - last_heartbeat))) / 3600 as avg_hours_since_heartbeat
            FROM fetch_accounts
        """).mappings().first()

        return {
            "database_size": db_size_results["current_size"] if db_size_results else "Unknown",
            "database_size_bytes": db_size_results["current_size_bytes"] if db_size_results else 0,
            "error_labels": error_labels,
            "error_counts": error_counts,
            "worker_stats": {
                "total_accounts": worker_results["total_accounts"] if worker_results else 0,
                "enabled_accounts": worker_results["enabled_accounts"] if worker_results else 0,
                "accounts_with_errors": worker_results["accounts_with_errors"] if worker_results else 0,
                "avg_hours_since_heartbeat": round(worker_results["avg_hours_since_heartbeat"] or 0, 1)
            }
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch system health report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/reports/av-stats")
def av_stats_report(request: Request, start_date: str = None, end_date: str = None):
    """Get anti-virus statistics report data"""
    if not require_login(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        date_format = get_user_date_format(request, date_only=True)

        # Calculate period based on date range for grouping
        days_diff = (end_dt - start_dt).days
        if days_diff <= 7:
            group_by = "DATE(created_at)"
        elif days_diff <= 90:
            group_by = "DATE_TRUNC('week', created_at)"
        else:
            group_by = "DATE_TRUNC('month', created_at)"

        # Get AV statistics
        av_results = query(f"""
            SELECT
                {group_by} as period_start,
                COUNT(CASE WHEN virus_detected = false THEN 1 END) as clean_count,
                COUNT(CASE WHEN virus_detected = true AND quarantined = true THEN 1 END) as quarantined_count,
                COUNT(CASE WHEN virus_detected = true AND quarantined = false THEN 1 END) as rejected_count
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
            GROUP BY {group_by}
            ORDER BY period_start
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().all()

        # Get total counts for the period
        total_results = query("""
            SELECT
                COUNT(CASE WHEN virus_detected = false THEN 1 END) as total_clean,
                COUNT(CASE WHEN virus_detected = true AND quarantined = true THEN 1 END) as total_quarantined,
                COUNT(CASE WHEN virus_detected = true AND quarantined = false THEN 1 END) as total_rejected
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().first()

        labels = []
        clean_counts = []
        quarantined_counts = []
        rejected_counts = []

        for row in av_results:
            if row["period_start"]:
                local_dt = convert_utc_to_user_timezone(row["period_start"], user_id)
                if days_diff <= 7:
                    labels.append(local_dt.strftime(date_format))
                elif days_diff <= 90:
                    week_end = local_dt + timedelta(days=6)
                    labels.append(f"{local_dt.strftime(date_format)} - {week_end.strftime(date_format)}")
                else:
                    labels.append(local_dt.strftime("%B %Y"))
            clean_counts.append(row["clean_count"])
            quarantined_counts.append(row["quarantined_count"])
            rejected_counts.append(row["rejected_count"])

        return {
            "clean_emails": total_results["total_clean"] if total_results else 0,
            "quarantined_emails": total_results["total_quarantined"] if total_results else 0,
            "rejected_emails": total_results["total_rejected"] if total_results else 0,
            "labels": labels,
            "clean_counts": clean_counts,
            "quarantined_counts": quarantined_counts,
            "rejected_counts": rejected_counts
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch AV statistics report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)