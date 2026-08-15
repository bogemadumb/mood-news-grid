"""Командная строка конвейера.

Каждая стадия — отдельная команда, чтобы любую можно было запустить и проверить отдельно.
Пока реализована только check-sources: диагностика источников до того, как что-то писать в базу.
"""
import re
import time
from pathlib import Path

import feedparser
import httpx
import typer
import yaml

app = typer.Typer(add_completion=False, help="Конвейер Mood News Grid")

SOURCES_PATH = Path(__file__).parent / "sources.yml"
MIN_BODY_LEN = 400
FOOTER_MARKERS = re.compile(
    r"все права защищены|свидетельство о регистрации|при использовании материалов|"
    r"ссылка на .{0,20}обязательна|copyright ©|роскомнадзор",
    re.I,
)


def load_config() -> dict:
    return yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))


def strip_html(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def entry_fulltext(entry, field: str | None) -> str:
    """Достать текст из записи ленты. Порядок: явное поле -> content -> summary."""
    if field:
        key = field.replace("-", "_")
        value = entry.get(key) or entry.get(field)
        if isinstance(value, list) and value:
            value = value[0].get("value", "")
        if value:
            return strip_html(str(value))
    if entry.get("content"):
        return strip_html(entry["content"][0].get("value", ""))
    return strip_html(entry.get("summary", ""))


@app.command("check-sources")
def check_sources(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Проверить каждую ленту: отвечает ли, сколько записей, сколько из них с годным текстом."""
    cfg = load_config()
    headers_base = {"User-Agent": cfg["user_agent"]}
    rows = []

    for src in cfg["sources"]:
        if not src.get("enabled", True):
            continue
        headers = {**headers_base, **(src.get("extra_headers") or {})}
        t0 = time.time()
        status, items, with_text, lengths, note = 0, 0, 0, [], ""
        try:
            r = httpx.get(
                src["feed_url"],
                headers=headers,
                timeout=cfg["request_timeout"],
                follow_redirects=True,
            )
            status = r.status_code
            if status == 200:
                parsed = feedparser.parse(r.content)
                items = len(parsed.entries)
                for entry in parsed.entries:
                    body = entry_fulltext(entry, src.get("fulltext_field"))
                    if len(body) >= MIN_BODY_LEN and not FOOTER_MARKERS.search(body[:500]):
                        with_text += 1
                        lengths.append(len(body))
                if parsed.bozo and not items:
                    note = f"не разобрался: {parsed.bozo_exception}"
        except Exception as exc:  # noqa: BLE001 — диагностика, любая ошибка интересна
            note = f"{type(exc).__name__}: {exc}"

        ms = int((time.time() - t0) * 1000)
        median = sorted(lengths)[len(lengths) // 2] if lengths else 0
        ratio = with_text / items if items else 0.0
        rows.append((src["code"], status, ms, items, with_text, ratio, median, note))
        time.sleep(cfg["pause_between_requests"])

    header = f"{'источник':<15}{'код':>5}{'мс':>7}{'записей':>9}{'с текстом':>11}{'доля':>7}{'медиана':>9}"
    typer.echo(header)
    typer.echo("-" * len(header))
    for code, status, ms, items, with_text, ratio, median, note in rows:
        typer.echo(
            f"{code:<15}{status:>5}{ms:>7}{items:>9}{with_text:>11}{ratio:>7.0%}{median:>9}"
        )
        if note:
            typer.echo(f"{'':<15}{note}")

    ok = sum(1 for r in rows if r[1] == 200 and r[4] > 0)
    typer.echo(f"\nГодных источников: {ok} из {len(rows)}")
    if verbose:
        typer.echo("Годный текст = длиннее 400 знаков и без маркеров юридического подвала.")


@app.command("migrate")
def migrate_cmd():
    """Накатить миграции базы."""
    from app.db import migrate

    applied = migrate()
    typer.echo(f"миграций накачено: {applied}")


@app.command("ingest")
def ingest_cmd(limit: int = typer.Option(40, help="сколько записей брать из каждой ленты")):
    """Собрать свежие новости из лент в базу."""
    from app.db import migrate
    from pipeline.ingest import ingest

    migrate()
    stats = ingest(limit_per_source=limit)
    typer.echo(
        f"\nпросмотрено {stats['seen']}, сохранено {stats['saved']}, "
        f"отсеяно гейтом {stats['rejected']}, повторов {stats['duplicates']}, "
        f"ошибок {stats['errors']}"
    )


@app.command("curate")
def curate_cmd(limit: int = typer.Option(30, help="сколько новостей держать в витрине")):
    """Отобрать витрину из собранного."""
    from pipeline.curate import curate

    curate(limit=limit)


@app.command("cycle")
def cycle_cmd(limit: int = typer.Option(40)):
    """Полный проход: собрать и отобрать."""
    from app.db import migrate
    from pipeline.curate import curate
    from pipeline.ingest import ingest

    migrate()
    stats = ingest(limit_per_source=limit)
    typer.echo("")
    curate()
    typer.echo(f"\nсобрано {stats['saved']}, отсеяно {stats['rejected']}, повторов {stats['duplicates']}")


@app.command("check-robots")
def check_robots():
    """Выкачать robots.txt каждого источника и показать, что там про нас."""
    cfg = load_config()
    out_dir = Path(__file__).resolve().parents[1] / "docs" / "robots"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src in cfg["sources"]:
        url = src["homepage_url"].rstrip("/") + "/robots.txt"
        try:
            r = httpx.get(url, headers={"User-Agent": cfg["user_agent"]}, timeout=15,
                          follow_redirects=True)
            body = r.text
            (out_dir / f"{src['code']}.txt").write_text(body, encoding="utf-8")
            star = re.search(r"User-agent:\s*\*(.*?)(?=\nUser-agent:|\Z)", body, re.S | re.I)
            block = star.group(1) if star else ""
            delay = re.search(r"Crawl-delay:\s*(\S+)", block, re.I)
            disallow_all = bool(re.search(r"^\s*Disallow:\s*/\s*$", block, re.M))
            typer.echo(
                f"{src['code']:<15} {r.status_code}  строк: {len(body.splitlines()):<5} "
                f"Crawl-delay: {delay.group(1) if delay else '—':<5} "
                f"полный запрет: {'ДА' if disallow_all else 'нет'}"
            )
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"{src['code']:<15} ошибка: {type(exc).__name__}: {exc}")
        time.sleep(1)

    typer.echo(f"\nФайлы сохранены в {out_dir}")


if __name__ == "__main__":
    app()
