"""Веб-приложение.

Пока здесь только каркас: главная-заглушка, /healthz и /version.
Грид новостей появится на втором этапе, экран сравнения — на третьем.
"""
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR, settings

app = FastAPI(
    title="Mood News Grid",
    root_path=settings.root_path,
    # Swagger по умолчанию тянется с cdn.jsdelivr.net — это внешний CDN,
    # который ломает CSP и может не открыться. Свой маршрут появится позже.
    docs_url=None,
    redoc_url=None,
)

templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"git_sha": settings.git_sha})


@app.get("/healthz")
def healthz():
    """Живость для watchdog. Пока база не заведена, отдаём заглушечное состояние."""
    db_exists = settings.db_path.exists()
    payload = {
        "ok": True,
        "stage": "1 - каркас",
        "db_exists": db_exists,
        "articles_ready": 0,
        "git_sha": settings.git_sha,
    }
    return JSONResponse(payload)


@app.get("/version")
def version():
    return {"version": "0.1.0", "git_sha": settings.git_sha}
