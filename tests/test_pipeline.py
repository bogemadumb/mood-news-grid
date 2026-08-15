"""Тесты конвейера и веба. Сеть не нужна — всё на фикстурах."""
import sqlite3

import pytest

from app.db import migrate, session
from pipeline.ingest import quality_gate
from pipeline.normalize import canonical_url, content_hash, long_sentence_ratio, strip_html


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "test.db"
    migrate(path)
    return path


def test_migrate_idempotent(db):
    """Повторный накат ничего не ломает — иначе рестарт сервиса убивал бы базу."""
    assert migrate(db) == 0
    with session(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sources", "articles", "facts", "moods", "rewrites", "fact_checks"} <= tables


def test_rewrite_without_llm_call_is_rejected(db):
    """Ключевой инвариант: опубликованный текст обязан ссылаться на вызов модели."""
    with session(db) as conn:
        conn.execute("INSERT INTO sources (code,title,homepage_url,feed_url) VALUES ('t','T','h','f')")
        conn.execute(
            """INSERT INTO articles (source_id,url,url_canon,title,fetched_at,content_hash)
               VALUES (1,'u','u','t','now','h')"""
        )
        conn.execute(
            """INSERT INTO moods (code,title_ru,system_prompt) VALUES ('joyful','Радостно','p')"""
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO rewrites (article_id,mood_code,generator,llm_call_id,created_at)
                   VALUES (1,'joyful','llm',NULL,'now')"""
            )
        # А детерминированному «Скелету» вызов модели не нужен — он собран кодом.
        conn.execute(
            """INSERT INTO rewrites (article_id,mood_code,generator,llm_call_id,created_at)
               VALUES (1,'joyful','code',NULL,'now')"""
        )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://www.rbc.ru/news/123/?utm_source=tg&x=1", "https://rbc.ru/news/123?x=1"),
        ("https://lenta.ru/news/2026/08/15/abc/", "https://lenta.ru/news/2026/08/15/abc"),
        ("http://m.3dnews.ru/1146873?ysclid=zzz", "https://3dnews.ru/1146873"),
    ],
)
def test_canonical_url(raw, expected):
    assert canonical_url(raw) == expected


def test_same_article_different_links_same_hash():
    body = "Банк России снизил ставку до 15% годовых."
    assert content_hash("Заголовок", body) == content_hash("Заголовок ", f" {body} ")


def test_quality_gate_rejects_footer():
    footer = ("Все права защищены. Свидетельство о регистрации СМИ выдано Роскомнадзором. "
              "При использовании материалов ссылка на издание обязательна. " * 6)
    ok, reason = quality_gate("Банк снизил ставку", footer)
    assert not ok
    assert "подвал" in reason


def test_quality_gate_rejects_short():
    ok, reason = quality_gate("Заголовок", "Слишком коротко.")
    assert not ok and "знаков" in reason


def test_quality_gate_accepts_real_news():
    title = "Банк России снизил ключевую ставку до 15 процентов"
    body = (
        "Банк России 19 августа снизил ключевую ставку до 15 процентов годовых, "
        "объяснив решение замедлением инфляции в последние месяцы. "
        "Регулятор отметил, что кредитная активность населения остаётся сдержанной. "
        "Аналитики ожидали именно такого шага и не исключают дальнейшего смягчения. "
        "Следующее заседание совета директоров запланировано на осень этого года. "
    ) * 2
    ok, reason = quality_gate(title, body)
    assert ok, reason


def test_long_sentence_ratio_separates_navigation_from_text():
    menu = "Главная. Новости. Спорт. Погода. Контакты. Реклама."
    text = ("Регулятор объяснил решение замедлением инфляции в последние месяцы года. "
            "Аналитики ожидали именно такого шага и не исключают дальнейшего смягчения политики.")
    assert long_sentence_ratio(menu) < 0.6 <= long_sentence_ratio(text)


def test_strip_html_kills_scripts():
    assert "alert" not in strip_html("<p>Текст</p><script>alert(1)</script>")
