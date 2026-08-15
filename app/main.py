"""Веб-приложение.

Сайт только читает базу. Ни одного вызова модели в обработчике запроса:
все тона предгенерированы конвейером, поэтому переключение будет мгновенным,
а страница не зависит от того, жив ли сейчас провайдер.
"""
import hashlib
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import repository
from app.config import BASE_DIR, settings

app = FastAPI(
    title="Mood News Grid",
    root_path=settings.root_path,
    # Swagger по умолчанию тянется с cdn.jsdelivr.net — внешний CDN нам не нужен.
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

MONTHS = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def source_hue(code: str) -> int:
    """Цвет издания — из хеша его кода. Чужие логотипы не показываем принципиально."""
    return int(hashlib.sha256(code.encode()).hexdigest(), 16) % 360


def human_time(iso: str | None) -> str:
    if not iso:
        return "без даты"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return "без даты"
    now = datetime.now(timezone.utc)
    delta = now - dt
    if delta.total_seconds() < 3600:
        return f"{max(1, int(delta.total_seconds() // 60))} мин назад"
    if delta.days < 1:
        return f"{int(delta.total_seconds() // 3600)} ч назад"
    if delta.days < 7:
        return f"{delta.days} дн назад"
    return f"{dt.day} {MONTHS[dt.month - 1]}"


def make_lead(text: str | None, limit: int = 180) -> str:
    if not text:
        return ""
    clean = " ".join(text.split())
    if len(clean) <= limit:
        return clean
    cut = clean[:limit]
    space = cut.rfind(" ")
    return cut[: space if space > 60 else limit].rstrip(" ,.;:—-") + "…"


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    articles = repository.showcase_articles()
    for a in articles:
        a["hue"] = source_hue(a["source_code"])
        a["published_human"] = human_time(a["published_at"])
        a["lead"] = make_lead(a["rewrite_source_text"])

    counts = repository.counters()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "articles": articles,
            "c": counts,
            "last_fetch": human_time(counts.get("last_fetch")),
            "git_sha": settings.git_sha,
        },
    )


@app.get("/healthz")
def healthz():
    """Живость для watchdog: пустая витрина — это отказ, а не «всё хорошо»."""
    counts = repository.counters()
    ok = counts["showcase"] >= settings.min_ready_articles
    payload = {
        "ok": ok,
        "articles_total": counts["articles_total"],
        "showcase": counts["showcase"],
        "sources_used": counts["sources_used"],
        "published_rewrites": counts["published_rewrites"],
        "last_fetch": counts["last_fetch"],
        "git_sha": settings.git_sha,
    }
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.get("/version")
def version():
    return {"version": "0.2.0", "stage": "2 - сбор и витрина", "git_sha": settings.git_sha}


@app.get("/api/articles")
def api_articles():
    """Данные видно из браузера, а не только после клонирования репозитория."""
    return {"articles": repository.showcase_articles()}


@app.get("/api/sources")
def api_sources():
    return {"sources": repository.sources_overview()}
