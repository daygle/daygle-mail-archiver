"""
Shared Jinja2 templates configuration with custom filters
"""
from pathlib import Path
from fastapi.templating import Jinja2Templates
from utils.timezone import convert_utc_to_user_timezone, format_datetime


# Determine templates directory
BASE_DIR = Path(__file__).parent.parent
templates_dir = BASE_DIR / "templates" if (BASE_DIR / "templates").exists() else BASE_DIR.parent / "templates"

# Create templates instance
templates = Jinja2Templates(directory=str(templates_dir))


# Custom Jinja2 filters
def to_user_timezone_filter(utc_datetime, user_id):
    """Jinja2 filter to convert UTC datetime to user's timezone"""
    return convert_utc_to_user_timezone(utc_datetime, user_id)


def format_user_datetime_filter(utc_datetime, user_id, date_format=None):
    """Jinja2 filter to format datetime in user's timezone and format"""
    return format_datetime(utc_datetime, user_id, date_format)


# Register filters
templates.env.filters['to_user_timezone'] = to_user_timezone_filter
templates.env.filters['format_user_datetime'] = format_user_datetime_filter
