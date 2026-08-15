"""Единственное место в проекте, где пишется SQL.

Всё остальное ходит в базу только через эти функции — иначе запросы расползаются
по шаблонам и обработчикам, и потом невозможно ответить на вопрос «где хранятся данные».
"""
from app.db import session


def showcase_articles(limit: int = 30, only_ready: bool = True) -> list[dict]:
    """Новости для грида.

    По умолчанию отдаются только те, где готовы ВСЕ активные тона. Иначе проверяющий
    переключит режим и часть карточек исчезнет, а по клику получит плашку «не готово» —
    и это будет выглядеть как поломка, а не как честная очередь.
    """
    ready_clause = ""
    if only_ready:
        # Готова та новость, у которой опубликованы все РАЗРЕШЁННЫЕ ей тона.
        # У материалов о гибели людей их всего один — и это полный комплект.
        ready_clause = "AND a.moods_ready >= a.moods_expected AND a.moods_ready > 0"
    with session() as conn:
        rows = conn.execute(
            f"""SELECT a.id, a.title, a.url, a.author, a.published_at, a.source_len,
                      a.rewrite_source_text, a.extraction_method, a.url_http_status,
                      a.moods_ready, a.moods_expected, a.sensitivity,
                      s.code AS source_code, s.title AS source_title, s.homepage_url
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.in_showcase = 1 {ready_clause}
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
                 (SELECT COUNT(*) FROM articles
                    WHERE moods_ready >= moods_expected AND moods_ready > 0) AS ready,
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
    """Новости с полным комплектом разрешённых им тонов."""
    with session() as conn:
        rows = conn.execute(
            "SELECT id FROM articles WHERE moods_ready >= moods_expected AND moods_ready > 0"
        ).fetchall()
        return {r["id"] for r in rows}


def stats() -> dict:
    """Цифры для страницы статистики. Всё считается из базы, ничего не зашито."""
    with session() as conn:
        row = conn.execute("""
            SELECT
              (SELECT COUNT(*) FROM articles) AS articles,
              (SELECT COUNT(*) FROM articles WHERE in_showcase=1) AS showcase,
              (SELECT COUNT(*) FROM articles WHERE sensitivity='death') AS sensitive,
              (SELECT COUNT(*) FROM articles WHERE sensitivity='death' AND in_showcase=1) AS sensitive_showcase,
              (SELECT COUNT(*) FROM rewrites) AS attempts,
              (SELECT COUNT(*) FROM rewrites WHERE status='published') AS published,
              (SELECT COUNT(*) FROM rewrites WHERE status='failed') AS failed,
              (SELECT COUNT(*) FROM rewrites WHERE status='published' AND attempt=1) AS first_try,
              (SELECT COUNT(*) FROM hallucinations) AS hallucinations,
              (SELECT COUNT(*) FROM llm_calls) AS llm_calls,
              (SELECT COUNT(*) FROM llm_calls WHERE error IS NOT NULL) AS llm_errors,
              (SELECT COUNT(*) FROM incidents) AS incidents,
              (SELECT MAX(fetched_at) FROM articles) AS last_fetch
        """).fetchone()
        data = dict(row)
        data["by_mood"] = [dict(r) for r in conn.execute("""
            SELECT m.code, m.title_ru, m.temperature,
                   COUNT(r.id) AS attempts,
                   SUM(CASE WHEN r.status='published' THEN 1 ELSE 0 END) AS published,
                   ROUND(AVG(CASE WHEN r.status='published' THEN r.style_distance END), 3) AS distance,
                   ROUND(AVG(CASE WHEN r.status='published' THEN r.check_score END), 1) AS fpi
            FROM moods m LEFT JOIN rewrites r ON r.mood_code = m.code
            WHERE m.is_active=1 GROUP BY m.code ORDER BY m.sort_order""")]
        data["verdicts"] = [dict(r) for r in conn.execute(
            "SELECT verdict, COUNT(*) n FROM rewrites WHERE verdict IS NOT NULL"
            " GROUP BY verdict ORDER BY n DESC")]
        data["sources"] = [dict(r) for r in conn.execute("""
            SELECT s.code, s.title, COUNT(a.id) articles,
                   SUM(CASE WHEN a.in_showcase=1 THEN 1 ELSE 0 END) showcase,
                   GROUP_CONCAT(DISTINCT a.extraction_method) methods
            FROM sources s LEFT JOIN articles a ON a.source_id=s.id
            WHERE s.enabled=1 GROUP BY s.code ORDER BY articles DESC""")]
        data["hard_kinds"] = [dict(r) for r in conn.execute("""
            SELECT verdict, COUNT(*) n FROM fact_checks WHERE severity=2
            GROUP BY verdict ORDER BY n DESC LIMIT 8""")]
        data["no_call"] = conn.execute(
            "SELECT COUNT(*) n FROM rewrites WHERE status='published'"
            " AND generator='llm' AND llm_call_id IS NULL").fetchone()["n"]
        return data


def failed_rewrites(limit: int = 40) -> list[dict]:
    """Забракованные варианты для карантина. Показывать свой брак — самый честный аргумент."""
    with session() as conn:
        rows = conn.execute("""
            SELECT r.id, r.article_id, r.mood_code, r.attempt, r.verdict, r.style_distance,
                   r.body_rewritten, a.title, m.title_ru AS mood_title,
                   (SELECT GROUP_CONCAT(note, ' · ') FROM fact_checks fc
                     WHERE fc.rewrite_id=r.id AND fc.severity=2) AS hard_notes,
                   (SELECT GROUP_CONCAT(surface, ' · ') FROM hallucinations h
                     WHERE h.rewrite_id=r.id) AS invented
            FROM rewrites r JOIN articles a ON a.id=r.article_id
            JOIN moods m ON m.code=r.mood_code
            WHERE r.status='failed'
            ORDER BY r.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]
