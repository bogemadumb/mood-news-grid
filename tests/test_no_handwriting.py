"""Доказательство, что настроенческие тексты не написаны руками.

ТЗ прямо запрещает готовить варианты заранее вручную. Обычно это «доказывают» рассказом
в README. Здесь запрет проверяется на данных: каждый опубликованный текст обязан ссылаться
на конкретный вызов модели, а сам текст обязан посимвольно содержаться в её ответе.

Тесты работают на боевой базе, если она есть, и пропускаются на пустой.
"""
import json

import pytest

from app.db import migrate, session

# На чистом клоне базы ещё нет: заводим пустую, чтобы тесты аккуратно пропустились.
migrate()


def _rows(query, params=()):
    with session() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


published = _rows("SELECT * FROM rewrites WHERE status='published'")
pytestmark = pytest.mark.skipif(not published, reason="база пустая, нечего проверять")


def test_every_published_text_has_a_model_call():
    """Ни одного опубликованного текста без записи вызова модели."""
    orphans = [r["id"] for r in published if r["generator"] == "llm" and not r["llm_call_id"]]
    assert not orphans, f"текстов без вызова модели: {len(orphans)}"


def test_model_call_actually_succeeded():
    calls = {r["id"]: r for r in _rows("SELECT * FROM llm_calls")}
    for rewrite in published:
        if not rewrite["llm_call_id"]:
            continue
        call = calls.get(rewrite["llm_call_id"])
        assert call is not None, f"вызов {rewrite['llm_call_id']} потерян"
        assert call["error"] is None, f"опубликован текст из вызова с ошибкой: {call['error']}"


def test_published_body_comes_from_the_model_response():
    """Текст должен посимвольно находиться в сыром ответе модели.

    Это и есть главная проверка: подставить сюда написанный руками текст невозможно,
    не подделав заодно сохранённый ответ модели.
    """
    calls = {r["id"]: r for r in _rows("SELECT * FROM llm_calls")}
    mismatched = []
    for rewrite in published:
        call = calls.get(rewrite["llm_call_id"] or -1)
        if not call or not call["response_text"]:
            continue
        body = (rewrite["body_rewritten"] or "").strip()
        raw = call["response_text"]
        # Модель отдаёт JSON, поэтому в сыром ответе переводы строк и кавычки экранированы.
        if body and body not in raw and json.dumps(body, ensure_ascii=False)[1:-1] not in raw:
            mismatched.append(rewrite["id"])
    assert not mismatched, f"текстов, которых нет в ответе модели: {len(mismatched)}"


def test_prompt_was_not_tuned_per_article():
    """Один тон — один промпт. Иначе можно было бы подогнать инструкцию под каждую новость."""
    rows = _rows(
        """SELECT mood_code, prompt_version, COUNT(DISTINCT prompt_sha256) n
           FROM rewrites WHERE status='published' GROUP BY mood_code, prompt_version"""
    )
    bad = [r for r in rows if r["n"] > 1]
    assert not bad, f"промпт менялся между новостями: {bad}"


def test_invariant_line_for_readme():
    """Строка-инвариант, которая идёт в README и на страницу статистики."""
    total = len(published)
    with_call = sum(1 for r in published if r["llm_call_id"])
    print(f"\nтекстов без записи вызова модели: {total - with_call} из {total}")
    assert with_call == total
