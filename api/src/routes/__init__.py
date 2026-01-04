from pathlib import Path
from fastapi.templating import Jinja2Templates

# Set up templates directory - handle both Docker and local environments
BASE_DIR = Path(__file__).parent.parent
if (BASE_DIR / "templates").exists():
    # Running from Docker (/app/routes with /app/templates)
    templates_dir = BASE_DIR / "templates"
else:
    # Running locally from src/routes with ../../templates
    templates_dir = BASE_DIR.parent / "templates"

templates = Jinja2Templates(directory=str(templates_dir))
