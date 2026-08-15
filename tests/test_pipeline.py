"""Тесты конвейера и веба. Сеть не нужна — всё на фикстурах."""
import sqlite3

import pytest

from app.db import MIGRATIONS_DIR, migrate, session
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
        # версия равна числу миграций в каталоге, а не константе
        expected = max(int(f.name.split('_')[0]) for f in MIGRATIONS_DIR.glob('*.sql'))
        assert conn.execute("PRAGMA user_version").fetchone()[0] == expected
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


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"value": "<p>Текст</p>"}, "Текст"),
        ({"name": "Иван Витченко"}, "Иван Витченко"),
        ([{"value": "<b>А</b>"}, {"value": "Б"}], "А Б"),
        (None, ""),
        (42, "42"),
    ],
)
def test_strip_html_survives_feedparser_shapes(raw, expected):
    """Ленты отдают одно и то же поле то строкой, то словарём, то списком словарей.

    Ловится только на живых данных: у части записей Коммерсанта автор приходит словарём,
    и первая версия сбора падала на них с TypeError посреди прогона.
    """
    assert strip_html(raw) == expected


# --- Подсветка фактов: единственное место, где собирается HTML ---

def test_render_spans_escapes_everything():
    """Заголовки приходят из интернета. Публичное демо не должно быть XSS-полигоном."""
    from app.highlight import render_spans

    text = '<img src=x onerror=alert(1)> Ставка 15% </mark><script>alert(2)</script>'
    start = text.index("15%")
    out = str(render_spans(text, [{"start": start, "end": start + 3, "kind": "percent", "id": "1"}]))
    # Проверяем не наличие подстрок, а то, что не осталось ни одного ЖИВОГО тега,
    # кроме наших: слово onerror как обычный текст безвредно, опасен работающий атрибут.
    import re as _re

    tags = _re.findall(r"<[^>]+>", out)
    assert all(t.startswith("<mark") or t == "</mark>" for t in tags), tags
    assert out.count("<mark") == 1 and out.count("</mark>") == 1
    assert "&lt;img" in out and "&lt;script&gt;" in out


def test_render_spans_marks_the_right_fragment():
    from app.highlight import render_spans

    text = "Ставка снижена до 15% годовых"
    out = str(render_spans(text, [{"start": 18, "end": 21, "kind": "percent", "id": "f1"}]))
    assert ">15%</mark>" in out
    assert 'data-fact="f1"' in out


def test_find_spans_skips_overlaps():
    from app.highlight import find_spans

    text = "Банк России снизил ставку"
    spans = find_spans(text, [("1", "org", "Банк России"), ("2", "loc", "России")])
    assert len(spans) == 1


def test_negation_flip_detected_but_style_allowed():
    """Ирония добавляет «не» — это стиль. Пропавшее «не» у того же глагола — подлог."""
    import datetime

    from pipeline.facts.check import check, verdict

    pub = datetime.datetime(2026, 8, 15)
    orig = "Мельникова завоевала бронзу в Загребе. Результат составил 13,700 балла."
    ironic = ("Мельникова, судя по всему, решила не отвлекаться по мелочам и завоевала бронзу "
              "в Загребе. Результат — 13,700 балла, и это уже не сенсация.")
    assert verdict(check(orig, ironic, pub)) == "PASS"

    orig2 = "США и КНР не смогли договориться по тарифам."
    flipped = "США и КНР смогли договориться по тарифам."
    report = check(orig2, flipped, pub)
    assert verdict(report) == "FAIL"
    assert ("negation_dropped", "смочь") in report["neg_flips"]


def test_find_spans_extends_truncated_quote_to_word_boundary():
    """Цитаты приходят в отчёт обрезанными до 40 знаков — подсветка не должна рвать слово."""
    from app.highlight import find_spans

    text = "«В течение дня ударными беспилотными летательными аппаратами поражены склады»"
    truncated = "В течение дня ударными беспилотными лета"
    span = find_spans(text, [("q1", "quote", truncated)])[0]
    assert text[span["start"]:span["end"]].endswith("летательными")


@pytest.mark.parametrize(
    "a,b,expected",
    [
        ("Санчеса", "Санчес", True),          # родительный падеж против именительного
        ("Педро Санчес", "Санчес", True),     # сокращение до фамилии
        ("Эльвиры Набиуллиной", "Эльвира Набиуллина", True),
        ("Санчес", "Набиуллина", False),      # подмена спикера — настоящее нарушение
        ("Санчес", "", False),                # атрибуция потеряна
    ],
)
def test_same_speaker_survives_declension(a, b, expected):
    """natasha отдаёт спикера в падеже предложения.

    Сравнение сырых строк давало «speaker_changed» на идентичной цитате и блокировало
    публикацию: в оригинале «по словам Санчеса», в пересказе «Санчес заявил».
    """
    from pipeline.facts.check import same_speaker

    assert same_speaker(a, b) is expected


def test_speaker_platform_is_not_a_speaker():
    """«Разместила на своей странице в соцсети X» — говорящий не соцсеть.

    Разбор принимал название площадки за спикера, и корректная атрибуция в пересказе
    («Аюсо заявила») считалась подменой. Ловится только на живой новости.
    """
    from pipeline.facts.check import compare_speakers, plausible_speaker

    assert not plausible_speaker("X")
    assert not plausible_speaker("Telegram")
    assert plausible_speaker("Аюсо")

    orig = {"lemmas": {"аюсо", "санчес", "испания"}}
    # мусорная атрибуция в оригинале + имя из текста в пересказе — не нарушение
    assert compare_speakers("X", "Аюсо", orig) == "ok"
    # имя, которого в оригинале нет вообще, — выдумка
    assert compare_speakers("X", "Меркель", orig) == "speaker_changed"
    # атрибуция потеряна — мягкое замечание, не блокировка
    assert compare_speakers("Аюсо", None, orig) == "speaker_lost"


def test_foreign_surname_lemma_guess_does_not_break_match():
    """pymorphy3 считает «Аюсо» прилагательным и лемматизирует в «аюсый».

    Поэтому сравнение идёт по двум формам сразу — лемме и самому слову.
    """
    from pipeline.facts.check import same_speaker

    assert same_speaker("Аюсо", "Исабель Диас Аюсо")
    assert not same_speaker("Аюсо", "Санчес")


@pytest.mark.parametrize(
    "text,should_change",
    [
        ("Военные ударили по складам. Оставайтесь на связи с РБК в «Максе».", True),
        ("Ставка снижена до 15%.\nПодписывайтесь на наш телеграм-канал.", True),
        ("Новость. Следите за нами в соцсетях. Подписывайтесь на рассылку.", True),
        ("Обычная новость без хвоста. Второе предложение тоже обычное.", False),
        ("Компания подписка на сервис выросла на 12% за год по данным отчёта.", False),
    ],
)
def test_strip_promo_tail(text, should_change):
    """Рекламный хвост издания — не факт новости.

    «Оставайтесь на связи с РБК в „Максе“» давало два «пропавших факта» (РБК и Макс)
    в каждом пересказе, который этот хвост справедливо выбрасывал. Корректные тексты
    браковались; на сервере из-за этого не публиковалось вообще ничего.
    """
    from pipeline.normalize import strip_promo_tail

    result = strip_promo_tail(text)
    assert (result != text) is should_change
    assert result, "текст не должен исчезать целиком"


def test_platform_drop_is_soft_but_source_swap_is_hard():
    """Площадка публикации и источник новости — разные вещи.

    «Сообщил в своём канале в „Максе“» — канал публикации: его пропажа не искажает событие.
    А подмена Reuters на ТАСС меняет, кто это сообщил, и обязана блокировать публикацию.
    """
    import datetime

    from pipeline.facts.check import check, verdict

    pub = datetime.datetime(2026, 8, 15)

    orig = ("Губернатор Андрей Воробьев сообщил в своем канале в «Максе», "
            "что угрозы жизни ребенка сейчас нет.")
    without_platform = "Андрей Воробьев сообщил, что угрозы жизни ребенка сейчас нет."
    assert verdict(check(orig, without_platform, pub)) in ("PASS", "WARN")

    orig2 = "США и КНР не договорились по тарифам, сообщает Reuters."
    swapped = "США и КНР не договорились по тарифам, сообщает ТАСС."
    assert verdict(check(orig2, swapped, pub)) == "FAIL"


@pytest.mark.parametrize(
    "title,body,expected",
    [
        ("Скончалась журналист Аида Леванова", "Журналист умерла на 78-м году жизни.", "death"),
        ("Велогонщик погиб в 19 лет",
         "19-летний велогонщик Финли Тарлинг погиб, столкнувшись с машиной.", "death"),
        ("В результате теракта пострадали люди", "Взрыв произошёл утром.", "death"),
        # Ловушки: слова о смерти как метафора — блокировать нельзя
        ("Убийственный ценник на смартфон", "Флагман стоит 200 тысяч рублей.", "none"),
        ("Гибель урожая из-за засухи", "Аграрии сообщили о потере трети пшеницы.", "none"),
        ("Мёртвый сезон на рынке", "Продажи квартир упали на 12%.", "none"),
        ("Банк России снизил ставку", "ЦБ снизил ставку до 15% годовых.", "none"),
    ],
)
def test_stop_crane(title, body, expected):
    """Стоп-кран: ирония над гибелью людей отключается, метафоры не блокируются.

    Появился не из осторожности: на живом прогоне модель трижды отказалась пересказывать
    бодро новость о гибели велогонщика и написала, что конвейеру нужен такой фильтр.
    """
    from pipeline.sensitivity import classify

    level, _ = classify(title, body)
    assert level == expected


def test_mood_allowed_rules():
    from pipeline.sensitivity import mood_allowed

    neutral = {"allowed_max_sensitivity": "any"}
    ironic = {"allowed_max_sensitivity": "none"}
    assert mood_allowed(neutral, "death")
    assert not mood_allowed(ironic, "death")
    assert mood_allowed(ironic, "none")
