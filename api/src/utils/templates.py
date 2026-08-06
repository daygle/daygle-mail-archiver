"""
Shared Jinja2 templates configuration with custom filters
"""
from pathlib import Path
from email.utils import getaddresses
from fastapi.templating import Jinja2Templates
from .timezone import convert_utc_to_user_timezone, format_datetime
from .i18n import get_gettext


# Determine templates directory
BASE_DIR = Path(__file__).parent.parent
templates_dir = BASE_DIR / "templates" if (BASE_DIR / "templates").exists() else BASE_DIR.parent / "templates"

# Create templates instance
_jinja_templates = Jinja2Templates(directory=str(templates_dir))


class TemplatesWrapper:
    """Wrapper around Jinja2Templates that injects a per-request `gettext` function
    into the template context (key: `gettext`). This avoids modifying every route.
    """
    def __init__(self, jinja_templates: Jinja2Templates):
        self._templates = jinja_templates
        # expose env so callers can still access filters and env
        self.env = jinja_templates.env

    def TemplateResponse(self, name: str, context: dict, status_code: int = 200):
        # Ensure we don't mutate caller's dict
        ctx = dict(context or {})
        request = ctx.get('request')
        lang = 'en'
        if request and "session" in request.scope:
            lang = request.session.get('language', 'en')

        # inject gettext callable for templates
        ctx['gettext'] = get_gettext(lang)

        # Render template immediately to ensure per-request context (including
        # the injected `gettext`) is used at render time. FastAPI's
        # Jinja2Templates.TemplateResponse defers rendering which may cause
        # the environment globals to be used instead of our per-request
        # context in some cases.
        from fastapi.responses import HTMLResponse
        template = self._templates.env.get_template(name)
        body = template.render(ctx)
        return HTMLResponse(content=body, status_code=status_code, media_type="text/html")


# Public templates object used across the app
templates = TemplatesWrapper(_jinja_templates)


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

def extract_emails_filter(value: str) -> str:
    """Jinja2 filter to extract only email addresses from a header value.

    Converts display-name + address strings such as
    ``John Doe <john@example.com>`` to just ``john@example.com``.
    Multiple comma-separated addresses are each stripped of their display
    name and rejoined with ``', '``.
    """
    if not value:
        return value
    parsed = getaddresses([value])
    addresses = [addr for _name, addr in parsed if addr]
    return ", ".join(addresses) if addresses else value


# Register filters
templates.env.filters['extract_emails'] = extract_emails_filter
