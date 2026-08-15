"""Сбор новостей из лент.

Каскад добора текста: останавливаемся на первой ступени, которая дала годный результат.
  1) поле полного текста прямо в ленте (у РБК оно есть, и это самый дешёвый путь)
  2) JSON-LD со страницы (articleBody)
  3) CSS-селектор конкретного издания
  4) trafilatura как универсальный запасной

Порядок ступеней подстраивается полем prefer в sources.yml: замеры показали,
что универсального победителя нет — на одном издании выигрывает селектор, на другом trafilatura.

Гейт качества обязателен: без него в базу уезжает юридический подвал сайта,
который по длине неотличим от новости.
"""
import functools
import gzip
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import feedparser
import httpx
import trafilatura
import yaml
from bs4 import BeautifulSoup

from app.config import BASE_DIR
from app.db import session
from pipeline.normalize import (
    canonical_url,
    content_hash,
    long_sentence_ratio,
    looks_like_footer,
    strip_html,
    text_sha256,
    to_iso_utc,
    word_count,
)

SOURCES_PATH = Path(__file__).parent / "sources.yml"
SNAPSHOT_DIR = BASE_DIR / "data" / "snapshots"

MIN_BODY_LEN = 400
MIN_TITLE_OVERLAP = 0.3
MIN_LONG_SENTENCE_RATIO = 0.6


@functools.lru_cache(maxsize=1)
def _morph():
    import pymorphy3

    return pymorphy3.MorphAnalyzer()


def lemmas(text: str) -> set[str]:
    morph = _morph()
    words = re.findall(r"[а-яёА-ЯЁa-zA-Z]{4,}", text)
    return {morph.parse(w)[0].normal_form for w in words[:400]}


def title_overlap(title: str, body: str) -> float:
    """Пересечение по леммам, а не по словоформам: заголовки-каламбуры иначе выбрасываются."""
    t, b = lemmas(title), lemmas(body)
    if not t:
        return 0.0
    return len(t & b) / len(t)


def load_sources() -> dict:
    return yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))


def sync_sources(conn, cfg: dict) -> None:
    """Конфиг из файла — источник правды. База догоняет его при каждом запуске."""
    for src in cfg["sources"]:
        conn.execute(
            """INSERT INTO sources (code, title, homepage_url, feed_url, fulltext_field,
                                    selector, prefer, extra_headers, why, enabled)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 title=excluded.title, feed_url=excluded.feed_url,
                 fulltext_field=excluded.fulltext_field, selector=excluded.selector,
                 prefer=excluded.prefer, extra_headers=excluded.extra_headers,
                 why=excluded.why, enabled=excluded.enabled""",
            (src["code"], src["title"], src["homepage_url"], src["feed_url"],
             src.get("fulltext_field"), src.get("selector"), src.get("prefer"),
             json.dumps(src.get("extra_headers") or {}, ensure_ascii=False),
             (src.get("why") or "").strip(), int(src.get("enabled", True))),
        )


def from_feed(entry, field: str | None) -> str:
    if field:
        key = field.replace("-", "_").replace(":", "_")
        value = entry.get(key) or entry.get(field)
        if isinstance(value, list) and value:
            value = value[0].get("value", "")
        if value:
            return strip_html(str(value))
    if entry.get("content"):
        return strip_html(entry["content"][0].get("value", ""))
    return ""


def from_jsonld(soup: BeautifulSoup) -> str:
    for tag in soup.find_all("script", type="application/ld+json"):
        if not tag.string or "articleBody" not in tag.string:
            continue
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError:
            match = re.search(r'"articleBody"\s*:\s*"(.*?)(?<!\\)"', tag.string, re.S)
            if match:
                return strip_html(match.group(1).replace("\\n", "\n"))
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("articleBody"):
                return strip_html(node["articleBody"])
    return ""


def from_selector(soup: BeautifulSoup, selector: str | None) -> str:
    if not selector:
        return ""
    nodes = soup.select(selector)
    return strip_html(" ".join(n.get_text(" ", strip=True) for n in nodes))


def from_trafilatura(html_text: str) -> str:
    return (trafilatura.extract(html_text, include_comments=False, include_tables=False) or "").strip()


def quality_gate(title: str, body: str) -> tuple[bool, str]:
    """Вернуть (годен, причина отказа)."""
    if len(body) < MIN_BODY_LEN:
        return False, f"короче {MIN_BODY_LEN} знаков ({len(body)})"
    if looks_like_footer(body):
        return False, "похоже на юридический подвал сайта"
    ratio = long_sentence_ratio(body)
    if ratio < MIN_LONG_SENTENCE_RATIO:
        return False, f"мало длинных предложений ({ratio:.2f})"
    overlap = title_overlap(title, body)
    if overlap < MIN_TITLE_OVERLAP:
        return False, f"текст не про заголовок (пересечение лемм {overlap:.2f})"
    return True, ""


def fetch_body(client: httpx.Client, entry, src: dict, headers: dict) -> tuple[str, str, str]:
    """Каскад. Возвращает (текст, каким способом взят, сырой html страницы)."""
    order = ["feed", "jsonld", "selector", "trafilatura"]
    prefer = src.get("prefer")
    if prefer in order:
        order.remove(prefer)
        order.insert(0, prefer)

    feed_text = from_feed(entry, src.get("fulltext_field"))
    if order[0] == "feed" and len(feed_text) >= MIN_BODY_LEN:
        return feed_text, "feed", ""

    html_text, soup = "", None
    best, method = feed_text, "feed"
    for step in order:
        if step == "feed":
            candidate = feed_text
        else:
            if not html_text:
                try:
                    page = client.get(entry.link, headers=headers, timeout=25, follow_redirects=True)
                    html_text = page.text
                    soup = BeautifulSoup(html_text, "lxml")
                except httpx.HTTPError:
                    break
            if step == "jsonld":
                candidate = from_jsonld(soup)
            elif step == "selector":
                candidate = from_selector(soup, src.get("selector"))
            else:
                candidate = from_trafilatura(html_text)
        if len(candidate) > len(best):
            best, method = candidate, step
        if len(best) >= MIN_BODY_LEN and step == order[0]:
            break
    return best, method, html_text


def save_snapshot(code: str, url_canon: str, html_text: str) -> tuple[str | None, str | None]:
    """Снимок страницы на диск: доказательство, что новость была именно такой."""
    if not html_text:
        return None, None
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{code}-{text_sha256(url_canon)[:16]}.html.gz"
    path = SNAPSHOT_DIR / name
    data = html_text.encode("utf-8")
    with gzip.open(path, "wb") as fh:
        fh.write(data)
    return str(path.relative_to(BASE_DIR)), text_sha256(html_text)


def ingest(limit_per_source: int = 40, verbose: bool = True) -> dict:
    cfg = load_sources()
    base_headers = {"User-Agent": cfg["user_agent"]}
    run_id = uuid.uuid4().hex[:12]
    started = datetime.now(timezone.utc).isoformat()
    stats = {"seen": 0, "saved": 0, "rejected": 0, "duplicates": 0, "errors": 0}
    rejects: list[tuple[str, str]] = []

    with session() as conn, httpx.Client(follow_redirects=True) as client:
        sync_sources(conn, cfg)
        conn.execute(
            "INSERT INTO pipeline_runs (run_id, kind, started_at) VALUES (?,?,?)",
            (run_id, "ingest", started),
        )

        for src in cfg["sources"]:
            if not src.get("enabled", True):
                continue
            row = conn.execute("SELECT id, etag, last_modified FROM sources WHERE code=?",
                               (src["code"],)).fetchone()
            headers = {**base_headers, **(src.get("extra_headers") or {})}
            cond = dict(headers)
            if row["etag"]:
                cond["If-None-Match"] = row["etag"]
            if row["last_modified"]:
                cond["If-Modified-Since"] = row["last_modified"]

            t0 = time.time()
            try:
                resp = client.get(src["feed_url"], headers=cond, timeout=cfg["request_timeout"])
            except httpx.HTTPError as exc:
                stats["errors"] += 1
                conn.execute(
                    "INSERT INTO incidents (run_id, kind, stage, source_or_provider, message, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (run_id, "feed_error", "ingest", src["code"], str(exc),
                     datetime.now(timezone.utc).isoformat()),
                )
                if verbose:
                    print(f"{src['code']:<14} ошибка: {exc}")
                continue

            if resp.status_code == 304:
                if verbose:
                    print(f"{src['code']:<14} 304, лента не менялась")
                continue

            conn.execute(
                "UPDATE sources SET etag=?, last_modified=?, last_fetch_at=? WHERE code=?",
                (resp.headers.get("ETag"), resp.headers.get("Last-Modified"),
                 datetime.now(timezone.utc).isoformat(), src["code"]),
            )

            parsed = feedparser.parse(resp.content)
            saved_here = passed_here = 0
            for entry in parsed.entries[:limit_per_source]:
                stats["seen"] += 1
                if not entry.get("link"):
                    continue
                url_canon = canonical_url(entry.link)
                if conn.execute("SELECT 1 FROM articles WHERE url_canon=?", (url_canon,)).fetchone():
                    stats["duplicates"] += 1
                    continue

                title = strip_html(entry.get("title", "")).strip()
                body, method, html_text = fetch_body(client, entry, src, headers)
                ok, reason = quality_gate(title, body)
                if not ok:
                    stats["rejected"] += 1
                    rejects.append((src["code"], reason))
                    continue
                passed_here += 1

                chash = content_hash(title, body)
                if conn.execute("SELECT 1 FROM articles WHERE content_hash=?", (chash,)).fetchone():
                    stats["duplicates"] += 1
                    continue

                snap_path, snap_hash = save_snapshot(src["code"], url_canon, html_text)
                author = strip_html(entry.get("author", "")) or None
                image = None
                if entry.get("media_content"):
                    image = entry["media_content"][0].get("url")
                elif entry.get("enclosures"):
                    image = entry["enclosures"][0].get("href")

                conn.execute(
                    """INSERT INTO articles
                       (source_id, guid, url, url_canon, title, body_text, rewrite_source_text,
                        source_text_sha256, source_len, author, image_url, published_at, fetched_at,
                        content_hash, word_count, extraction_method, extraction_quality,
                        raw_entry_json, snapshot_path, snapshot_sha256, status)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["id"], entry.get("id"), entry.link, url_canon, title, body, body,
                     text_sha256(body), len(body), author, image,
                     to_iso_utc(entry.get("published_parsed")),
                     datetime.now(timezone.utc).isoformat(), chash, word_count(body),
                     method, long_sentence_ratio(body),
                     json.dumps({k: str(v)[:2000] for k, v in dict(entry).items()
                                 if k in ("id", "title", "link", "published", "author")},
                                ensure_ascii=False),
                     snap_path, snap_hash, "new"),
                )
                saved_here += 1
                stats["saved"] += 1

            total_seen = min(len(parsed.entries), limit_per_source) or 1
            conn.execute(
                """INSERT INTO source_health (source_code, checked_at, ok, http_status, latency_ms,
                                              items, full_text_ratio, quality_gate_pass_ratio)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (src["code"], datetime.now(timezone.utc).isoformat(), 1, resp.status_code,
                 int((time.time() - t0) * 1000), len(parsed.entries),
                 passed_here / total_seen, passed_here / total_seen),
            )
            if verbose:
                print(f"{src['code']:<14} {resp.status_code}  записей {len(parsed.entries):>3}  "
                      f"сохранено {saved_here:>2}")
            time.sleep(cfg["pause_between_requests"])

        conn.execute(
            """UPDATE pipeline_runs SET finished_at=?, ok=1, items_in=?, items_out=?, errors=?
               WHERE run_id=?""",
            (datetime.now(timezone.utc).isoformat(), stats["seen"], stats["saved"],
             stats["errors"], run_id),
        )

    if verbose and rejects:
        print("\nПочему отсеяли (гейт качества):")
        seen_reasons: dict[str, int] = {}
        for code, reason in rejects:
            key = f"{code}: {reason.split('(')[0].strip()}"
            seen_reasons[key] = seen_reasons.get(key, 0) + 1
        for key, count in sorted(seen_reasons.items(), key=lambda x: -x[1])[:10]:
            print(f"  {count:>3} x {key}")

    return stats
