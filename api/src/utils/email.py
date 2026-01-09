"""
Email utility functions for sending alerts and notifications
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr
from typing import List, Optional
import ssl

from utils.db import query
from utils.logger import log
from utils.timezone import format_datetime


def get_smtp_config() -> dict:
    """
    Get SMTP configuration from database settings

    Returns:
        dict: SMTP configuration with keys: enabled, host, port, username, password, use_tls, from_email, from_name
    """
    settings_keys = [
        'smtp_enabled', 'smtp_host', 'smtp_port', 'smtp_username',
        'smtp_password', 'smtp_use_tls', 'smtp_from_email', 'smtp_from_name'
    ]

    settings = {}
    for key in settings_keys:
        result = query("SELECT value FROM settings WHERE key = :key", {"key": key}).mappings().first()
        settings[key] = result["value"] if result else ""

    # Convert string values to appropriate types
    return {
        'enabled': settings.get('smtp_enabled', 'false').lower() == 'true',
        'host': settings.get('smtp_host', ''),
        'port': int(settings.get('smtp_port', '587')),
        'username': settings.get('smtp_username', ''),
        'password': settings.get('smtp_password', ''),
        'use_tls': settings.get('smtp_use_tls', 'true').lower() == 'true',
        'from_email': settings.get('smtp_from_email', ''),
        'from_name': settings.get('smtp_from_name', 'Daygle Mail Archiver')
    }


def send_email(
    to_email: str,
    subject: str,
    body: str,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None
) -> bool:
    """
    Send an email using the configured SMTP settings

    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Plain text email body
        html_body: Optional HTML email body
        cc: Optional list of CC email addresses
        bcc: Optional list of BCC email addresses

    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    config = get_smtp_config()

    if not config['enabled']:
        log("warning", "Email", "SMTP is not enabled, skipping email send", "")
        return False

    if not config['host'] or not config['from_email']:
        log("error", "Email", "SMTP configuration incomplete - missing host or from_email", "")
        return False

    try:
        # Create message
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = formataddr((config['from_name'], config['from_email']))
        msg['To'] = to_email

        if cc:
            msg['Cc'] = ', '.join(cc)

        # Add plain text body
        msg.attach(MIMEText(body, 'plain'))

        # Add HTML body if provided
        if html_body:
            msg.attach(MIMEText(html_body, 'html'))

        # Create SMTP connection
        if config['use_tls']:
            server = smtplib.SMTP(config['host'], config['port'])
            server.starttls(context=ssl.create_default_context())
        else:
            server = smtplib.SMTP_SSL(config['host'], config['port'], context=ssl.create_default_context())

        # Login if credentials provided
        if config['username'] and config['password']:
            server.login(config['username'], config['password'])

        # Prepare recipients list
        recipients = [to_email]
        if cc:
            recipients.extend(cc)
        if bcc:
            recipients.extend(bcc)

        # Send email
        server.sendmail(config['from_email'], recipients, msg.as_string())
        server.quit()

        log("info", "Email", f"Email sent successfully to {to_email}", f"Subject: {subject}")
        return True

    except Exception as e:
        log("error", "Email", f"Failed to send email to {to_email}: {str(e)}", f"Subject: {subject}")
        return False


def send_alert_email(
    alert_type: str,
    subject: str,
    message: str,
    recipients: List[str]
) -> bool:
    """
    Send an alert email to multiple recipients

    Args:
        alert_type: Type of alert (e.g., 'error', 'warning', 'info')
        subject: Email subject
        message: Alert message
        recipients: List of recipient email addresses

    Returns:
        bool: True if all emails were sent successfully
    """
    if not recipients:
        log("warning", "Email", f"No recipients specified for {alert_type} alert", "")
        return False

    success_count = 0

    for email in recipients:
        # Create HTML body for alerts
        html_body = f"""
        <html>
        <body>
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <h2 style="color: {'#dc3545' if alert_type == 'error' else '#ffc107' if alert_type == 'warning' else '#17a2b8'}">
                    {alert_type.upper()} Alert
                </h2>
                <div style="background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin: 20px 0;">
                    {message.replace('\n', '<br>')}
                </div>
                <hr>
                <p style="color: #6c757d; font-size: 12px;">
                    This is an automated alert from Daygle Mail Archiver.<br>
                    Please do not reply to this email.
                </p>
            </div>
        </body>
        </html>
        """

        if send_email(email, subject, message, html_body):
            success_count += 1

    return success_count == len(recipients)


def test_smtp_connection(recipient_email: str, user_id: int = None) -> tuple[bool, str]:
    """
    Test SMTP connection and send a test email to the specified recipient

    Args:
        recipient_email: Email address to send the test email to
        user_id: User ID for timezone/date formatting (optional)

    Returns:
        tuple: (success: bool, message: str)
    """
    config = get_smtp_config()

    if not config['enabled']:
        return False, "SMTP is not enabled"

    if not config['host']:
        return False, "SMTP host is not configured"

    if not config['from_email']:
        return False, "From email address is not configured"

    if not recipient_email:
        return False, "Recipient email address is required"

    try:
        # Create SMTP connection
        if config['use_tls']:
            server = smtplib.SMTP(config['host'], config['port'])
            server.starttls(context=ssl.create_default_context())
        else:
            server = smtplib.SMTP_SSL(config['host'], config['port'], context=ssl.create_default_context())

        # Login if credentials provided
        if config['username'] and config['password']:
            server.login(config['username'], config['password'])

        # Send a test email
        from datetime import datetime, timezone
        
        # Format timestamp using user's preferences or global settings
        current_time = datetime.now(timezone.utc)
        formatted_time = format_datetime(current_time, user_id) if user_id else current_time.strftime('%Y-%m-%d %H:%M:%S UTC')
        
        test_subject = f"Daygle Mail Archiver - SMTP Test"
        test_body = f"""This is a test email from Daygle Mail Archiver.

SMTP Configuration Test Details:
- Host: {config['host']}
- Port: {config['port']}
- From: {config['from_email']}
- To: {recipient_email}
- TLS: {'Yes' if config['use_tls'] else 'No'}
- Time: {formatted_time}

If you received this email, your SMTP configuration is working correctly.
"""

        msg = MIMEText(test_body)
        msg['Subject'] = test_subject
        msg['From'] = formataddr((config['from_name'], config['from_email']))
        msg['To'] = recipient_email

        server.sendmail(config['from_email'], recipient_email, msg.as_string())
        server.quit()

        return True, f"SMTP connection successful - test email sent to {recipient_email}"

    except Exception as e:
        return False, f"SMTP test failed: {str(e)}"