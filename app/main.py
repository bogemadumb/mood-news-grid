"""Веб-приложение.

Сайт только читает базу. Ни одного вызова модели в обработчике запроса:
все тона предгенерированы конвейером, поэтому переключение будет мгновенным,
а страница не зависит от того, жив ли сейчас провайдер.
"""
import hashlib
import os
import re
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import db, highlight, repository
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


MIN_GRID = 6


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    # В ленту идут новости, у которых готовы все тона. Пока конвейер их не набрал,
    # показываем и остальные, помечая, что они в очереди: пустая страница выглядела бы
    # поломкой, а честная очередь — рабочим состоянием.
    articles = repository.showcase_articles()
    ready_ids = {a["id"] for a in articles}
    if len(articles) < MIN_GRID:
        articles = repository.showcase_articles(only_ready=False)
    for a in articles:
        a["hue"] = source_hue(a["source_code"])
        a["published_human"] = human_time(a["published_at"])
        a["lead"] = make_lead(a["rewrite_source_text"])
        a["is_ready"] = a["id"] in ready_ids

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
    ok = counts["ready"] >= settings.min_ready_articles
    payload = {
        "ok": ok,
        "articles_total": counts["articles_total"],
        "showcase": counts["showcase"],
        "ready": counts["ready"],
        "sources_used": counts["sources_used"],
        "published_rewrites": counts["published_rewrites"],
        "last_fetch": counts["last_fetch"],
        "git_sha": settings.git_sha,
    }
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.get("/version")
def version():
    return {"version": "0.3.0", "stage": "3 - тона и проверка фактов", "git_sha": settings.git_sha}


@app.get("/api/articles")
def api_articles():
    """Данные видно из браузера, а не только после клонирования репозитория."""
    return {"articles": repository.showcase_articles()}


@app.get("/api/sources")
def api_sources():
    return {"sources": repository.sources_overview()}


def _slug(text: str) -> str:
    table = str.maketrans("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                          "abvgdeejziyklmnoprstufhc4ww_y_eua")
    base = text.lower().translate(table)
    return re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:60] or "news"


@app.get("/n/{article_id}", response_class=HTMLResponse)
def article_page(request: Request, article_id: int, mood: str = "neutral"):
    """Экран сравнения: оригинал и переписанный текст рядом, с результатом сверки фактов."""
    article = repository.article_by_id(article_id)
    if not article:
        return HTMLResponse("<h1>404</h1><p>Такой новости нет.</p>", status_code=404)

    moods = repository.active_moods()
    codes = [m["code"] for m in moods]
    if mood not in codes:
        mood = codes[0] if codes else "neutral"
    mood_row = repository.mood_by_code(mood)

    rewrite = repository.published_rewrite(article_id, mood)
    if not rewrite:
        return HTMLResponse(
            templates.get_template("not_ready.html").render(
                request=request, article=article, mood=mood_row, moods=moods,
                git_sha=settings.git_sha),
            status_code=200,
        )

    source_text = article["rewrite_source_text"] or ""
    body_new = rewrite["body_rewritten"] or ""

    # Подсвечиваем только те факты, которые проверка нашла в обоих текстах:
    # подсветить «пропавший» факт справа физически нечем.
    kept = [c for c in repository.checks_of(rewrite["id"]) if c["verdict"] != "missing"]
    surfaces = []
    for i, c in enumerate(kept):
        kind, _, surface = (c["note"] or "").partition(": ")
        if surface:
            surfaces.append((f"f{i}", kind, surface))

    spans_left = highlight.find_spans(source_text, surfaces)
    spans_right = highlight.find_spans(body_new, surfaces)

    checks = repository.checks_of(rewrite["id"])
    total = len(checks)
    missing = sum(1 for c in checks if c["severity"] == 2)

    return templates.TemplateResponse(
        request,
        "article.html",
        {
            "article": article,
            "mood": mood_row,
            "moods": moods,
            "rewrite": rewrite,
            "attempts": repository.all_attempts(article_id, mood),
            "original_html": highlight.render_spans(source_text, spans_left),
            "rewritten_html": highlight.render_spans(body_new, spans_right),
            "check": {
                "total": total,
                "kept": total - missing,
                "passed": rewrite["verdict"] == "PASS",
                "style_distance": rewrite["style_distance"] or 0,
            },
            "hue": source_hue(article["source_code"]),
            "published_human": human_time(article["published_at"]),
            "git_sha": settings.git_sha,
        },
    )


@app.post("/n/{article_id}/regenerate")
def regenerate(article_id: int, mood: str = Form("neutral")):
    """Сгенерировать вариант заново прямо сейчас.

    Существует ради одного вопроса, который проверяющий задаст первым: «а вы не написали
    эти тексты руками заранее?». Здесь он нажимает кнопку и получает вариант, которого
    минуту назад не было, вместе с новым номером вызова модели и результатом проверки.

    Ограничение: живая генерация включается переменной ALLOW_LIVE_REGENERATE.
    На публичном демо она выключена по умолчанию — за ней стоит личная подписка автора,
    и раздавать её посетителям нельзя.
    """
    if os.getenv("ALLOW_LIVE_REGENERATE", "0") != "1":
        return JSONResponse(
            {"ok": False,
             "reason": "живая генерация выключена на этом стенде: за ней стоит личная "
                       "подписка автора. Как включить — написано в README."},
            status_code=503,
        )

    article = repository.article_by_id(article_id)
    mood_row = repository.mood_by_code(mood)
    if not article or not mood_row:
        return JSONResponse({"ok": False, "reason": "нет такой новости или режима"}, status_code=404)

    from pipeline.rewrite import run_attempt, save_attempt

    attempt = run_attempt(mood_row, article, 1, None, None)
    with db.session() as conn:
        rewrite_id = save_attempt(
            conn, article, mood_row, 1, attempt,
            os.getenv("LLM_PROVIDER", "echo"), os.getenv("CLAUDE_MODEL", "sonnet"),
            "published" if attempt.verdict == "PASS" else "failed",
        )
    return JSONResponse({
        "ok": attempt.verdict == "PASS",
        "verdict": attempt.verdict,
        "rewrite_id": rewrite_id,
        "llm_call_id": attempt.call_id,
        "style_distance": round(attempt.style_distance, 3),
        "title": attempt.title,
        "body": attempt.body,
        "violations": attempt.violations,
    })
