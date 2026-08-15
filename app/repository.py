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
