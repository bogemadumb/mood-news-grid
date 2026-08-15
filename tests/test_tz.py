"""Проверка соответствия техническому заданию — по пункту на тест.

Смысл файла: проверяющему не нужно верить README на слово и искать по интерфейсу.
Он запускает `pytest -k tz` и видит, что каждый пункт задания закрыт и чем именно.
Сеть и ключи не нужны: всё проверяется по базе и по коду.

Формулировки пунктов взяты из ТЗ дословно.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import session
from app.main import app

client = TestClient(app)


def rows(query, params=()):
    with session() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def one(query, params=()):
    with session() as conn:
        return conn.execute(query, params).fetchone()[0]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _plain(html: str) -> str:
    """Разметка без тегов: подсветка фактов режет текст на куски."""
    import html as html_mod
    import re

    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    return _norm(html_mod.unescape(text))


has_data = one("SELECT COUNT(*) FROM articles") > 0
needs_data = pytest.mark.skipif(not has_data, reason="база пустая: сначала make cycle")


# --- «Что нужно сделать», пункты 1-6 ---

@needs_data
def test_tz_p1_minimum_ten_real_news():
    """1. Получить минимум 10 реальных новостей из открытого источника."""
    total = one("SELECT COUNT(*) FROM articles")
    assert total >= 10, f"новостей всего {total}"
    # Реальность подтверждается ссылкой на внешний домен и сохранённой сырой записью ленты
    sample = rows("SELECT url, raw_entry_json FROM articles LIMIT 20")
    assert all(r["url"].startswith("http") for r in sample)
    assert all("bogemadumb" not in r["url"] for r in sample), "новости должны быть чужими"
    assert sum(1 for r in sample if r["raw_entry_json"]) >= len(sample) // 2


@needs_data
def test_tz_p2_stored_in_database():
    """2. Сохранить новости в базу или структурированное хранилище."""
    tables = {r["name"] for r in rows(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"articles", "sources", "rewrites", "fact_checks", "llm_calls"} <= tables
    assert one("SELECT COUNT(*) FROM sources WHERE enabled=1") >= 3


@needs_data
def test_tz_p3_grid_page():
    """3. Показать новости гридом."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text.count('<article class="card') >= 6


@needs_data
def test_tz_p4_mood_switch():
    """4. Сделать переключатель настроения."""
    moods = rows("SELECT code FROM moods WHERE is_active=1 AND is_service=0")
    assert len(moods) >= 4, "в ТЗ названы четыре режима"
    codes = {m["code"] for m in moods}
    assert {"neutral", "joyful", "sad", "ironic"} <= codes

    article = rows("""SELECT article_id FROM rewrites WHERE status='published'
                      GROUP BY article_id HAVING COUNT(DISTINCT mood_code) >= 2 LIMIT 1""")
    if not article:
        pytest.skip("пересказов пока меньше двух на одну новость")
    aid = article[0]["article_id"]
    page = client.get(f"/n/{aid}").text
    for code in ("neutral", "joyful", "sad", "ironic"):
        assert f"?mood={code}" in page, f"в переключателе нет режима {code}"


@needs_data
def test_tz_p5_original_and_rewritten_side_by_side():
    """5. По клику на новость показывать исходный текст и переписанный текст рядом."""
    pair = rows("""SELECT article_id, mood_code FROM rewrites
                   WHERE status='published' LIMIT 1""")
    if not pair:
        pytest.skip("нет ни одного опубликованного пересказа")
    aid, mood = pair[0]["article_id"], pair[0]["mood_code"]
    page = client.get(f"/n/{aid}?mood={mood}").text

    original = one("SELECT rewrite_source_text FROM articles WHERE id=?", (aid,))
    rewritten = one(
        "SELECT body_rewritten FROM rewrites WHERE article_id=? AND mood_code=? "
        "AND status='published'", (aid, mood))

    # Текст на странице разрезан тегами подсветки фактов, поэтому сравниваем
    # с разметкой, из которой теги убраны.
    plain = _plain(page)

    # Оригинал показывается ЦЕЛИКОМ, а не обрезком: проверяем и начало, и хвост
    assert _norm(original[:70]) in plain, "начала исходного текста нет на странице"
    assert _norm(original[-70:]) in plain, "исходный текст обрезан: хвоста нет на странице"
    assert _norm(rewritten[:70]) in plain, "переписанного текста нет на странице"
    assert 'class="col rewritten' in page and page.count('class="col') >= 2


@needs_data
def test_tz_p6_link_to_original_source():
    """6. Показывать ссылку на оригинальный источник.

    Ссылки проверяются у тех новостей, которые лента реально показывает: в грид попадают
    только материалы с полным комплектом разрешённых им тонов.
    """
    page = client.get("/").text
    shown = rows("""SELECT url FROM articles WHERE in_showcase=1
                    AND moods_ready >= moods_expected AND moods_ready > 0 LIMIT 5""") \
        or rows("SELECT url FROM articles WHERE in_showcase=1 LIMIT 5")
    assert shown, "витрина пуста"
    assert all(u["url"] in page for u in shown), "у показанной новости нет ссылки на источник"


# --- «Важная логика» ---

@needs_data
def test_tz_logic_no_handwritten_versions():
    """Нельзя заранее вручную написать новости в нужном настроении."""
    orphans = one("""SELECT COUNT(*) FROM rewrites
                     WHERE status='published' AND generator='llm' AND llm_call_id IS NULL""")
    assert orphans == 0, f"{orphans} опубликованных текстов без вызова модели"


@needs_data
def test_tz_logic_facts_are_checked():
    """Нужно сохранить факты: имена, даты, числа, места, цитаты."""
    published = one("SELECT COUNT(*) FROM rewrites WHERE status='published'")
    if not published:
        pytest.skip("нет опубликованных пересказов")
    # У каждого опубликованного варианта есть результаты сверки, и среди них нет жёстких
    unchecked = one("""SELECT COUNT(*) FROM rewrites r WHERE r.status='published'
                       AND NOT EXISTS (SELECT 1 FROM fact_checks fc WHERE fc.rewrite_id=r.id)""")
    assert unchecked == 0, f"{unchecked} опубликованных без единой проверки фактов"
    hard = one("""SELECT COUNT(*) FROM fact_checks fc JOIN rewrites r ON r.id=fc.rewrite_id
                  WHERE r.status='published' AND fc.severity=2""")
    assert hard == 0, f"опубликован текст с {hard} жёсткими нарушениями"
    invented = one("""SELECT COUNT(*) FROM hallucinations h JOIN rewrites r ON r.id=h.rewrite_id
                      WHERE r.status='published'""")
    assert invented == 0, f"опубликован текст с {invented} выдуманными фактами"


@needs_data
def test_tz_logic_provenance_is_visible():
    """Должно быть понятно, откуда пришла каждая новость."""
    orphan = one("""SELECT COUNT(*) FROM articles a
                    LEFT JOIN sources s ON s.id=a.source_id WHERE s.id IS NULL""")
    assert orphan == 0
    page = client.get("/").text
    titles = rows("""SELECT DISTINCT s.title FROM sources s JOIN articles a ON a.source_id=s.id
                     WHERE a.in_showcase=1
                       AND a.moods_ready >= a.moods_expected AND a.moods_ready > 0""") \
        or rows("""SELECT DISTINCT s.title FROM sources s JOIN articles a ON a.source_id=s.id
                   WHERE a.in_showcase=1 LIMIT 3""")
    assert titles, "витрина пуста"
    assert all(t["title"] in page for t in titles), "издание не подписано на карточке"


@needs_data
def test_tz_logic_real_news_only():
    """Реальные новости обязательны. Выдуманные тестовые не подходят."""
    domains = rows("""SELECT DISTINCT s.homepage_url FROM sources s
                      JOIN articles a ON a.source_id=s.id WHERE a.in_showcase=1""")
    assert len(domains) >= 2, "витрина должна собираться минимум из двух изданий"
    fake = one("""SELECT COUNT(*) FROM articles
                  WHERE url LIKE '%example.com%' OR url LIKE '%localhost%'
                     OR title LIKE '%тестовая новость%'""")
    assert fake == 0


def test_tz_prompt_lives_in_database_and_is_shown():
    """Пояснение к «как переписывается тон»: промпт не спрятан в коде."""
    prompts = rows("SELECT code, system_prompt FROM moods WHERE is_active=1")
    assert prompts and all(len(p["system_prompt"]) > 200 for p in prompts)
    pair = rows("SELECT article_id, mood_code FROM rewrites WHERE status='published' LIMIT 1")
    if pair:
        page = client.get(f"/n/{pair[0]['article_id']}?mood={pair[0]['mood_code']}").text
        assert "каким промптом это сделано" in page


def test_tz_no_external_cdn_anywhere():
    """Модель угроз: ни одного внешнего адреса в разметке публичных страниц."""
    import re
    from pathlib import Path

    from app.config import BASE_DIR

    allowed = ("bogemadumb.ru",)
    bad = []
    for tpl in (BASE_DIR / "app" / "templates").glob("*.html"):
        for match in re.finditer(r'(?:src|href)="(https?://[^"]+)"', tpl.read_text(encoding="utf-8")):
            url = match.group(1)
            if not any(a in url for a in allowed):
                bad.append(f"{tpl.name}: {url}")
    for css in (BASE_DIR / "app" / "static").glob("*.css"):
        for match in re.finditer(r'url\((https?://[^)]+)\)', css.read_text(encoding="utf-8")):
            bad.append(f"{css.name}: {match.group(1)}")
    assert not bad, "внешние ресурсы: " + "; ".join(bad)
