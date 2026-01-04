from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from utils.templates import templates

router = APIRouter()

def require_login(request: Request):
    return "user_id" in request.session

@router.get("/help")
def help_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    return templates.TemplateResponse(
        "help.html",
        {
            "request": request,
        },
    )
