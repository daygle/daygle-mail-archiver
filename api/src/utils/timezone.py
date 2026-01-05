"""
Timezone conversion utilities for displaying dates in user's preferred timezone
"""
from datetime import datetime
import pytz
from utils.db import query


def get_user_timezone(user_id: int) -> str:
    """
    Get the timezone preference for a specific user.
    Falls back to global timezone setting if user hasn't set a preference.
    
    Args:
        user_id: The ID of the user
        
    Returns:
        Timezone string (e.g., 'Australia/Melbourne')
    """
    # Try to get user's timezone preference
    user = query("SELECT timezone FROM users WHERE id = :id", {"id": user_id}).mappings().first()
    if user and user["timezone"]:
        return user["timezone"]
    
    # Fall back to global timezone setting
    setting = query("SELECT value FROM settings WHERE key = 'timezone'").mappings().first()
    if setting and setting["value"]:
        return setting["value"]
    
    # Default fallback
    return "Australia/Melbourne"


def get_global_timezone() -> str:
    """
    Get the global timezone setting.
    
    Returns:
        Timezone string (e.g., 'Australia/Melbourne')
    """
    setting = query("SELECT value FROM settings WHERE key = 'timezone'").mappings().first()
    if setting and setting["value"]:
        return setting["value"]
    return "Australia/Melbourne"


def convert_utc_to_timezone(utc_datetime, target_timezone: str):
    """
    Convert a UTC datetime to a specific timezone.
    
    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive)
        target_timezone: Timezone string (e.g., 'Australia/Melbourne')
        
    Returns:
        Datetime object converted to target timezone
    """
    if utc_datetime is None:
        return None
    
    # If datetime is naive, assume it's UTC
    if utc_datetime.tzinfo is None:
        utc_datetime = pytz.utc.localize(utc_datetime)
    
    # Convert to target timezone
    tz = pytz.timezone(target_timezone)
    return utc_datetime.astimezone(tz)


def convert_utc_to_user_timezone(utc_datetime, user_id: int):
    """
    Convert a UTC datetime to the user's preferred timezone.
    
    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive)
        user_id: The ID of the user
        
    Returns:
        Datetime object converted to user's timezone
    """
    if utc_datetime is None:
        return None
    
    user_tz = get_user_timezone(user_id)
    return convert_utc_to_timezone(utc_datetime, user_tz)


def format_datetime(utc_datetime, user_id: int, date_format: str = None, time_format: str = None):
    """
    Convert a UTC datetime to user's timezone and format it according to user's preference.
    
    Args:
        utc_datetime: A datetime object (can be timezone-aware or naive)
        user_id: The ID of the user
        date_format: Optional date format string. If not provided, uses user's preference.
        time_format: Optional time format string. If not provided, uses user's preference.
        
    Returns:
        Formatted datetime string
    """
    if utc_datetime is None:
        return ""
    
    # Convert to user's timezone
    local_datetime = convert_utc_to_user_timezone(utc_datetime, user_id)
    
    # Get date format preference if not provided
    if date_format is None:
        # First get global date format
        global_setting = query("SELECT value FROM settings WHERE key = 'date_format'").mappings().first()
        date_format = global_setting["value"] if global_setting else "%d/%m/%Y"
        
        # Override with user's date format if set
        user = query("SELECT date_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user and user["date_format"]:
            date_format = user["date_format"]
    
    # Get time format preference if not provided
    if time_format is None:
        # First get global time format
        global_setting = query("SELECT value FROM settings WHERE key = 'time_format'").mappings().first()
        time_format = global_setting["value"] if global_setting else "%H:%M"
        
        # Override with user's time format if set
        user = query("SELECT time_format FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user and user["time_format"]:
            time_format = user["time_format"]
    
    # Combine date and time format
    full_format = f"{date_format} {time_format}"
    
    # Format the datetime
    return local_datetime.strftime(full_format)
