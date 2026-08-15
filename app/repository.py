"""Единственное место в проекте, где пишется SQL.

Всё остальное ходит в базу только через эти функции — иначе запросы расползаются
по шаблонам и обработчикам, и потом невозможно ответить на вопрос «где хранятся данные».
"""
from app.db import session


def showcase_articles(limit: int = 30) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            """SELECT a.id, a.title, a.url, a.author, a.published_at, a.source_len,
                      a.rewrite_source_text, a.extraction_method, a.url_http_status,
                      s.code AS source_code, s.title AS source_title, s.homepage_url
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.in_showcase = 1
               ORDER BY a.published_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def article_by_id(article_id: int) -> dict | None:
    with session() as conn:
        row = conn.execute(
            """SELECT a.*, s.code AS source_code, s.title AS source_title, s.homepage_url
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.id = ?""",
            (article_id,),
        ).fetchone()
        return dict(row) if row else None


def counters() -> dict:
    """Цифры для футера, /healthz и будущей страницы статистики."""
    with session() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM articles) AS articles_total,
                 (SELECT COUNT(*) FROM articles WHERE in_showcase=1) AS showcase,
                 (SELECT COUNT(DISTINCT source_id) FROM articles WHERE in_showcase=1) AS sources_used,
                 (SELECT MAX(fetched_at) FROM articles) AS last_fetch,
                 (SELECT COUNT(*) FROM rewrites WHERE status='published') AS published_rewrites"""
        ).fetchone()
        return dict(row)


def sources_overview() -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            """SELECT s.code, s.title, s.homepage_url, s.feed_url, s.why, s.prefer,
                      COUNT(a.id) AS articles,
                      SUM(CASE WHEN a.in_showcase=1 THEN 1 ELSE 0 END) AS in_showcase
               FROM sources s LEFT JOIN articles a ON a.source_id = s.id
               WHERE s.enabled = 1
               GROUP BY s.code ORDER BY articles DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def active_moods() -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT * FROM moods WHERE is_active=1 AND is_service=0 ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]


def mood_by_code(code: str) -> dict | None:
    with session() as conn:
        row = conn.execute("SELECT * FROM moods WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def published_rewrite(article_id: int, mood_code: str) -> dict | None:
    with session() as conn:
        row = conn.execute(
            """SELECT * FROM rewrites
               WHERE article_id=? AND mood_code=? AND status='published'
               ORDER BY attempt DESC LIMIT 1""",
            (article_id, mood_code),
        ).fetchone()
        return dict(row) if row else None


def all_attempts(article_id: int, mood_code: str) -> list[dict]:
    """Все попытки, включая провальные: журнал и есть доказательство работы проверки."""
    with session() as conn:
        rows = conn.execute(
            """SELECT r.attempt, r.verdict, r.style_distance, r.status,
                      (SELECT GROUP_CONCAT(note, '; ') FROM fact_checks fc
                        WHERE fc.rewrite_id = r.id AND fc.severity = 2) AS hard_notes,
                      (SELECT GROUP_CONCAT(surface, '; ') FROM hallucinations h
                        WHERE h.rewrite_id = r.id) AS invented
               FROM rewrites r
               WHERE r.article_id=? AND r.mood_code=?
               ORDER BY r.attempt""",
            (article_id, mood_code),
        ).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            problems = []
            if item.pop("hard_notes", None):
                problems.append("факты: " + r["hard_notes"][:120])
            if item.pop("invented", None):
                problems.append("выдумано: " + r["invented"][:80])
            if r["verdict"] == "COPY":
                problems.append("почти дословный повтор оригинала")
            elif r["verdict"] in ("TOO_LONG", "TOO_SHORT"):
                problems.append("длина вышла за границы")
            item["problem"] = "; ".join(problems)
            out.append(item)
        return out


def facts_of(article_id: int) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT id, kind, surface, char_start, char_end FROM facts WHERE article_id=?",
            (article_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def checks_of(rewrite_id: int) -> list[dict]:
    with session() as conn:
        rows = conn.execute(
            "SELECT verdict, severity, found_surface, note FROM fact_checks WHERE rewrite_id=?",
            (rewrite_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def ready_article_ids() -> set[int]:
    """Новости, у которых готовы все активные тона. Только они идут в грид."""
    with session() as conn:
        active = conn.execute(
            "SELECT COUNT(*) n FROM moods WHERE is_active=1 AND is_service=0").fetchone()["n"]
        rows = conn.execute(
            """SELECT article_id FROM rewrites WHERE status='published'
               GROUP BY article_id HAVING COUNT(DISTINCT mood_code) >= ?""", (active,)).fetchall()
        return {r["article_id"] for r in rows}
