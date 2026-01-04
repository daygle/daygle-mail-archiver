from pathlib import Path
from fastapi.templating import Jinja2Templates

# Set up templates directory - go up from src/ to api/templates
BASE_DIR = Path(__file__).parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
