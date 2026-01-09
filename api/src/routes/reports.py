from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from collections import defaultdict
from typing import List, Dict, Any
from datetime import datetime, timedelta, date

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
            # Convert user_id to int for database queries
            user_id_int = int(user_id)
            user = query("SELECT date_format FROM users WHERE id = :id", {"id": user_id_int}).mappings().first()
            if user and user["date_format"]:
                date_format = user["date_format"]
        except (ValueError, TypeError):
            pass
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
            # Convert user_id to int for database queries
            user_id_int = int(user_id)
            user = query("SELECT time_format FROM users WHERE id = :id", {"id": user_id_int}).mappings().first()
            if user and user["time_format"]:
                time_format = user["time_format"]
        except (ValueError, TypeError):
            pass
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
            # Expand to full-day bounds (inclusive) so selecting a date includes that full day of data
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, 0, 0, 0)
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, 23, 59, 59, 999999)
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        if user_id is not None:
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                user_id = None
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

            email_counts.append(int(row["email_count"] or 0))
            virus_counts.append(int(row["virus_count"] or 0))
            sources_counts.append(int(row["sources_count"] or 0))

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
    # Temporarily disable auth for testing
    # if not require_login(request):
    #     return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            # Expand to inclusive day bounds so selecting a date includes that whole day
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, 0, 0, 0)
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, 23, 59, 59, 999999)
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")  # May be None for testing
        if user_id is not None:
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                user_id = None
        date_format = get_user_date_format(request, date_only=True)

        # Get account sync data
        results = query("""
            SELECT
                fa.name,
                fa.account_type,
                fa.enabled,
                fa.last_success,
                fa.last_error,
                CASE
                    WHEN fa.last_heartbeat IS NOT NULL THEN EXTRACT(EPOCH FROM (NOW() - fa.last_heartbeat)) / 3600
                    ELSE 0
                END as hours_since_heartbeat,
                COUNT(e.id) as emails_synced_today
            FROM fetch_accounts fa
            LEFT JOIN emails e ON e.source = fa.name AND DATE(e.created_at) = CURRENT_DATE
            GROUP BY fa.id, fa.name, fa.account_type, fa.enabled, fa.last_success, fa.last_error, fa.last_heartbeat
            ORDER BY fa.name
        """).mappings().all()

        accounts = []
        for row in results:
            try:
                last_success = None
                value = row["last_success"]

                if value:
                    if isinstance(value, str):
                        # Normalize Z → +00:00
                        value = value.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(value)
                    else:
                        # Already a datetime object
                        dt = value

                    last_success = convert_utc_to_user_timezone(dt, user_id).strftime(get_user_date_format(request))

                # Safely convert hours_since_heartbeat to float
                try:
                    hours_value = row["hours_since_heartbeat"]
                    if hours_value is None:
                        hours_float = 0.0
                    elif isinstance(hours_value, str):
                        hours_float = float(hours_value) if hours_value.strip() else 0.0
                    else:
                        hours_float = float(hours_value)
                    hours_rounded = round(hours_float, 1)
                except (ValueError, TypeError) as e:
                    log("error", "Reports", f"Error converting hours_since_heartbeat: {hours_value}, error: {e}", "")
                    hours_rounded = 0.0

                # Safely convert emails_synced_today to int
                try:
                    emails_value = row["emails_synced_today"]
                    if emails_value is None:
                        emails_int = 0
                    elif isinstance(emails_value, str):
                        emails_int = int(emails_value) if emails_value.strip() else 0
                    else:
                        emails_int = int(emails_value)
                except (ValueError, TypeError) as e:
                    log("error", "Reports", f"Error converting emails_synced_today: {emails_value}, error: {e}", "")
                    emails_int = 0

                accounts.append({
                    "name": row["name"],
                    "type": row["account_type"],
                    "enabled": bool(row["enabled"]),  # Convert SQLite integer to boolean
                    "last_success": last_success,
                    "last_error": row["last_error"],
                    "hours_since_heartbeat": hours_rounded,
                    "emails_today": emails_int
                })
            except Exception as e:
                log("error", "Reports", f"Error processing account row: {row}, error: {e}", "")
                continue

        # Get sync trends over time
        trend_results = query("""
            SELECT
                DATE(created_at) as sync_date,
                source,
                COUNT(*) as email_count
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
            GROUP BY DATE(created_at), source
            ORDER BY sync_date, source
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().all()

        # Organize trend data
        sources = set()
        trend_data = defaultdict(lambda: defaultdict(int))

        for row in trend_results:
            # Parse sync_date which may be a string or a date object
            sync_val = row.get("sync_date")
            sync_date = None
            if isinstance(sync_val, str):
                try:
                    sync_date = datetime.strptime(sync_val, "%Y-%m-%d").date()
                except Exception:
                    try:
                        sync_date = datetime.fromisoformat(sync_val).date()
                    except Exception:
                        sync_date = None
            elif isinstance(sync_val, datetime):
                sync_date = sync_val.date()
            elif isinstance(sync_val, date):
                sync_date = sync_val
            else:
                try:
                    sync_date = datetime.fromisoformat(str(sync_val)).date()
                except Exception:
                    sync_date = None

            if sync_date:
                date_str = convert_utc_to_user_timezone(sync_date, user_id).strftime(date_format)
            else:
                date_str = str(sync_val)

            source = row["source"]
            sources.add(source)
            
            # Safely convert email_count
            try:
                count_value = row["email_count"]
                if count_value is None:
                    email_count = 0
                elif isinstance(count_value, str):
                    email_count = int(count_value) if count_value.strip() else 0
                else:
                    email_count = int(count_value)
            except (ValueError, TypeError):
                email_count = 0
                
            trend_data[date_str][source] = email_count

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

        # If no accounts found, return empty structure for UI testing
        if not accounts:
            return {
                "accounts": [],
                "trend_labels": [],
                "trend_datasets": []
            }

        return {
            "accounts": accounts,
            "trend_labels": chart_labels,
            "trend_datasets": chart_datasets
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch account activity report for user '{username}': {str(e)}", "")
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
            # Expand to inclusive day bounds so the selected day(s) are included in the report
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, 0, 0, 0)
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, 23, 59, 59, 999999)
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")
        if user_id is not None:
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                user_id = None
        date_format = get_user_date_format(request, date_only=True)

        # Database growth over time (simplified - would need historical data for accurate growth)
        db_size_results = query("""
            SELECT
                pg_size_pretty(pg_database_size(current_database())) as current_size,
                pg_database_size(current_database()) as current_size_bytes
        """).mappings().first()

        # Safely extract database size
        db_size = "Unknown"
        db_size_bytes = 0
        if db_size_results:
            db_size = db_size_results.get("current_size", "Unknown")
            try:
                size_value = db_size_results.get("current_size_bytes")
                if size_value is None:
                    db_size_bytes = 0
                elif isinstance(size_value, str):
                    db_size_bytes = int(size_value) if size_value.strip() else 0
                else:
                    db_size_bytes = int(size_value)
            except (ValueError, TypeError):
                db_size_bytes = 0

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
            
            # Safely convert error_count
            try:
                count_value = row["error_count"]
                if count_value is None:
                    error_count = 0
                elif isinstance(count_value, str):
                    error_count = int(count_value) if count_value.strip() else 0
                else:
                    error_count = int(count_value)
            except (ValueError, TypeError):
                error_count = 0
                
            error_counts.append(error_count)

        # Worker status summary
        worker_results = query("""
            SELECT
                COUNT(*) as total_accounts,
                COUNT(CASE WHEN enabled THEN 1 END) as enabled_accounts,
                COUNT(CASE WHEN last_error IS NOT NULL THEN 1 END) as accounts_with_errors,
                AVG(EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) / 3600) as avg_hours_since_heartbeat
            FROM fetch_accounts
        """).mappings().first()

        # Safely extract worker stats
        worker_stats = {
            "total_accounts": 0,
            "enabled_accounts": 0,
            "accounts_with_errors": 0,
            "avg_hours_since_heartbeat": 0.0
        }
        
        if worker_results:
            for key in worker_stats:
                try:
                    value = worker_results[key]
                    if value is None:
                        continue
                    elif isinstance(value, str):
                        if key == "avg_hours_since_heartbeat":
                            worker_stats[key] = round(float(value) if value.strip() else 0.0, 1)
                        else:
                            worker_stats[key] = int(value) if value.strip() else 0
                    else:
                        if key == "avg_hours_since_heartbeat":
                            worker_stats[key] = round(float(value), 1)
                        else:
                            worker_stats[key] = int(value)
                except (ValueError, TypeError):
                    pass

        return {
            "database_size": db_size,
            "database_size_bytes": db_size_bytes,
            "error_labels": error_labels,
            "error_counts": error_counts,
            "worker_stats": worker_stats
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch system health report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)


@router.get("/api/reports/av-stats")
def av_stats_report(request: Request, start_date: str = None, end_date: str = None):
    """Get anti-virus statistics report data"""
    # Temporarily disable auth for testing
    # if not require_login(request):
    #     return JSONResponse({"error": "Unauthorized"}, status_code=401)

    try:
        # Validate date parameters
        if not start_date or not end_date:
            return JSONResponse({"error": "start_date and end_date are required"}, status_code=400)

        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            # Expand to inclusive day bounds so the selected dates' entire days are included
            start_dt = datetime(start_dt.year, start_dt.month, start_dt.day, 0, 0, 0)
            end_dt = datetime(end_dt.year, end_dt.month, end_dt.day, 23, 59, 59, 999999)
        except ValueError:
            return JSONResponse({"error": "Invalid date format. Use YYYY-MM-DD"}, status_code=400)

        if start_dt > end_dt:
            return JSONResponse({"error": "start_date must be before end_date"}, status_code=400)

        user_id = request.session.get("user_id")  # May be None for testing
        if user_id is not None:
            try:
                user_id = int(user_id)
            except (ValueError, TypeError):
                user_id = None
        date_format = get_user_date_format(request, date_only=True)

        # Calculate period based on date range for grouping (SQLite compatible)
        days_diff = (end_dt - start_dt).days
        # For now, use daily grouping to avoid SQLite date function issues
        group_by = "DATE(created_at)"

        # Get AV statistics
        av_results = query(f"""
            SELECT
                {group_by} as period_start,
                COUNT(CASE WHEN NOT virus_detected THEN 1 END) as clean_count,
                COUNT(CASE WHEN virus_detected THEN 1 END) as quarantined_count,
                0 as rejected_count
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
            GROUP BY {group_by}
            ORDER BY period_start
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().all()

        # Get total counts for the period
        total_results = query("""
            SELECT
                COUNT(CASE WHEN NOT virus_detected THEN 1 END) as total_clean,
                COUNT(CASE WHEN virus_detected THEN 1 END) as total_quarantined,
                0 as total_rejected
            FROM emails
            WHERE created_at >= :start_date AND created_at <= :end_date
        """, {"start_date": start_dt, "end_date": end_dt}).mappings().first()

        labels = []
        clean_counts = []
        quarantined_counts = []
        rejected_counts = []

        for row in av_results:
            if row["period_start"]:
                # Convert string date back to datetime for timezone conversion
                try:
                    period_dt = datetime.strptime(str(row["period_start"]), "%Y-%m-%d").date()
                    # Handle timezone conversion for authenticated user
                    if user_id:
                        local_dt = convert_utc_to_user_timezone(period_dt, user_id)
                    else:
                        # Use default timezone for testing
                        from utils.timezone import convert_utc_to_timezone
                        local_dt = convert_utc_to_timezone(period_dt, "Australia/Melbourne")
                    
                    # Format the date according to user preferences
                    labels.append(local_dt.strftime(date_format))
                except (ValueError, TypeError):
                    # Fallback to string representation if conversion fails
                    labels.append(str(row["period_start"]))
            clean_counts.append(int(row["clean_count"] or 0))
            quarantined_counts.append(int(row["quarantined_count"] or 0))
            rejected_counts.append(int(row["rejected_count"] or 0))

        return {
            "clean_emails": int(total_results["total_clean"] or 0) if total_results else 0,
            "quarantined_emails": int(total_results["total_quarantined"] or 0) if total_results else 0,
            "rejected_emails": int(total_results["total_rejected"] or 0) if total_results else 0,
            "labels": labels,
            "clean_counts": clean_counts,
            "quarantined_counts": quarantined_counts,
            "rejected_counts": rejected_counts
        }
    except Exception as e:
        username = request.session.get("username", "unknown")
        log("error", "Reports", f"Failed to fetch AV statistics report for user '{username}': {str(e)}", "")
        return JSONResponse({"error": "Failed to load data"}, status_code=500)