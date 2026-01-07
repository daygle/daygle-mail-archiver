"""
Alert system for managing system notifications and email alerts
"""
from typing import List, Optional
from utils.db import query, execute
from utils.logger import log
from utils.email import send_alert_email


def create_alert(
    alert_type: str,
    title: str,
    message: str,
    details: Optional[str] = None,
    send_email: bool = True,
    trigger_key: Optional[str] = None
) -> int:
    """
    Create a new alert

    Args:
        alert_type: Type of alert ('error', 'warning', 'info', 'success') - can be overridden by trigger_key
        title: Alert title
        message: Alert message
        details: Optional detailed information
        send_email: Whether to send email notification
        trigger_key: Optional trigger key to check if alert should be created and get severity from

    Returns:
        int: Alert ID
    """
    if alert_type not in ['error', 'warning', 'info', 'success']:
        raise ValueError("Invalid alert type")

    # If trigger_key is provided, look up the configured alert_type and check if enabled
    actual_alert_type = alert_type
    if trigger_key:
        try:
            result = query("SELECT alert_type, enabled FROM alert_triggers WHERE trigger_key = :key", {"key": trigger_key}).mappings().first()
            if result:
                if not result["enabled"]:
                    # Trigger is disabled, don't create alert
                    log("info", "Alert", f"Alert trigger '{trigger_key}' is disabled, skipping alert creation", "")
                    return 0
                # Use the configured alert_type from the database
                actual_alert_type = result["alert_type"]
        except Exception as e:
            log("error", "Alert", f"Failed to check alert trigger settings for '{trigger_key}': {str(e)}", "")
            # If we can't check the trigger, use the provided alert_type

    # Check if the alert type is globally enabled
    # Note: Global alert type filtering is now handled by trigger enable/disable
    # if not _is_alert_type_enabled(actual_alert_type):
    #     log("info", "Alert", f"Alert type '{actual_alert_type}' is globally disabled, skipping alert creation", "")
    #     return 0

    try:
        result = execute("""
            INSERT INTO alerts (alert_type, title, message, details)
            VALUES (:alert_type, :title, :message, :details)
            RETURNING id
        """, {
            "alert_type": actual_alert_type,
            "title": title,
            "message": message,
            "details": details
        })

        alert_id = result.fetchone()[0]

        log(actual_alert_type, "Alert", f"Alert created: {title}", f"ID: {alert_id}")

        # Send email if requested and alert type is enabled
        # Note: Global alert type filtering is now handled by trigger enable/disable
        if send_email:
            _send_alert_email(alert_id, actual_alert_type, title, message)

        return alert_id

    except Exception as e:
        log("error", "Alert", f"Failed to create alert '{title}': {str(e)}", "")
        raise


def _is_alert_trigger_enabled(trigger_key: str) -> bool:
    """
    Check if a specific alert trigger is enabled.

    Args:
        trigger_key: The trigger key to check

    Returns:
        bool: True if the trigger is enabled
    """
    try:
        result = query("SELECT enabled FROM alert_triggers WHERE trigger_key = :key", {"key": trigger_key}).mappings().first()
        
        if result:
            return result["enabled"]
        else:
            # If trigger doesn't exist, default to enabled
            log("warning", "Alert", f"Alert trigger '{trigger_key}' not found in database, defaulting to enabled", "")
            return True
    
    except Exception as e:
        log("error", "Alert", f"Failed to check alert trigger settings for '{trigger_key}': {str(e)}", "")
        # Default to enabled on error
        return True


def get_alerts(
    limit: int = 50,
    offset: int = 0,
    alert_type: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    include_details: bool = False
) -> List[dict]:
    """
    Get alerts with optional filtering

    Args:
        limit: Maximum number of alerts to return
        offset: Number of alerts to skip
        alert_type: Filter by alert type
        acknowledged: Filter by acknowledged status
        include_details: Whether to include details field

    Returns:
        List of alert dictionaries
    """
    where_conditions = []
    params = {"limit": limit, "offset": offset}

    if alert_type:
        where_conditions.append("alert_type = :alert_type")
        params["alert_type"] = alert_type

    if acknowledged is not None:
        where_conditions.append("acknowledged = :acknowledged")
        params["acknowledged"] = acknowledged

    where_clause = ""
    if where_conditions:
        where_clause = "WHERE " + " AND ".join(where_conditions)

    details_field = ", details" if include_details else ""

    results = query(f"""
        SELECT id, alert_type, title, message{details_field}, acknowledged,
               email_sent, created_at, acknowledged_at, acknowledged_by
        FROM alerts
        {where_clause}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """, params).mappings().all()

    return [dict(row) for row in results]


def acknowledge_alert(alert_id: int, user_id: int) -> bool:
    """
    Mark an alert as acknowledged

    Args:
        alert_id: ID of the alert to acknowledge
        user_id: ID of the user acknowledging the alert

    Returns:
        bool: True if successful
    """
    try:
        result = execute("""
            UPDATE alerts
            SET acknowledged = TRUE, acknowledged_at = NOW(), acknowledged_by = :user_id
            WHERE id = :alert_id AND acknowledged = FALSE
        """, {"alert_id": alert_id, "user_id": user_id})

        if result.rowcount > 0:
            log("info", "Alert", f"Alert {alert_id} acknowledged by user {user_id}", "")
            return True
        else:
            log("warning", "Alert", f"Alert {alert_id} not found or already acknowledged", "")
            return False

    except Exception as e:
        log("error", "Alert", f"Failed to acknowledge alert {alert_id}: {str(e)}", "")
        return False


def get_unacknowledged_count() -> int:
    """
    Get count of unacknowledged alerts

    Returns:
        int: Number of unacknowledged alerts
    """
    try:
        result = query("SELECT COUNT(*) as count FROM alerts WHERE acknowledged = FALSE").mappings().first()
        return result["count"] if result else 0
    except Exception as e:
        log("error", "Alert", f"Failed to get unacknowledged count: {str(e)}", "")
        return 0


def _send_alert_email(alert_id: int, alert_type: str, title: str, message: str) -> None:
    """
    Send email notification for an alert

    Args:
        alert_id: Alert ID
        alert_type: Alert type
        title: Alert title
        message: Alert message
    """
    try:
        # Note: Global alert type filtering is now handled by trigger enable/disable
        # First check if the alert type is globally enabled
        # if not _is_alert_type_enabled(alert_type):
        #     log("info", "Alert", f"Alert type '{alert_type}' is globally disabled, skipping email for alert {alert_id}", "")
        #     return

        # Get admin users with email addresses and email notifications enabled
        admin_users = query("""
            SELECT email FROM users
            WHERE role = 'administrator' AND email IS NOT NULL AND email != '' AND enabled = TRUE 
            AND email_notifications = TRUE
        """).mappings().all()

        recipients = [user["email"] for user in admin_users]

        if not recipients:
            log("warning", "Alert", f"No admin email addresses found for alert {alert_id}", "")
            return

        subject = f"Daygle Mail Archiver Alert: {title}"

        # Send the alert email
        if send_alert_email(alert_type, subject, message, recipients):
            # Mark email as sent
            execute("UPDATE alerts SET email_sent = TRUE WHERE id = :alert_id", {"alert_id": alert_id})
            log("info", "Alert", f"Email sent for alert {alert_id} to {len(recipients)} recipients", "")
        else:
            log("error", "Alert", f"Failed to send email for alert {alert_id}", "")

    except Exception as e:
        log("error", "Alert", f"Error sending alert email for alert {alert_id}: {str(e)}", "")


def cleanup_old_alerts(days: int = 90) -> int:
    """
    Delete old acknowledged alerts

    Args:
        days: Delete alerts older than this many days

    Returns:
        int: Number of alerts deleted
    """
    try:
        result = execute("""
            DELETE FROM alerts
            WHERE acknowledged = TRUE AND created_at < NOW() - make_interval(days => :days)
        """, {"days": days})

        deleted_count = result.rowcount
        if deleted_count > 0:
            log("info", "Alert", f"Cleaned up {deleted_count} old acknowledged alerts", "")

        return deleted_count

    except Exception as e:
        log("error", "Alert", f"Failed to cleanup old alerts: {str(e)}", "")
        return 0