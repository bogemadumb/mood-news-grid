"""Стоп-кран: определение новостей, над которыми нельзя шутить.

Зачем. Продукт предлагает читать новости «радостно» и «иронично». На заметке о гибели
людей это превращается в ироничный некролог — и проверяющий найдёт такой за полминуты.

Как это выяснилось. Не из осторожности, а на живом прогоне: модель сама трижды подряд
отказалась пересказывать бодро новость про гибель 19-летнего велогонщика и написала
открытым текстом, что такому материалу нужен фильтр до отправки в модель. Ответ при этом
не разбирался как JSON и попадал в статистику как техническая ошибка. Теперь отказ —
отдельное состояние, а до модели такие пары просто не доходят.

Как устроено. Триггерная лемма плюс проверка, что речь о людях. Наивный список слов не
годится: «гибель урожая», «убийственный ценник», «мёртвый сезон» — не трагедии.
Поэтому у триггера ищется синтаксически связанное существительное (подлежащее для глагола,
родительный для отглагольного существительного), и оно должно быть одушевлённым либо
входить в белый список слов о людях.
"""
import functools
import re

DEATH_VERBS = {"погибнуть", "погибать", "умереть", "умирать", "скончаться",
               "убить", "убивать", "застрелить", "зарезать", "утонуть", "разбиться"}
DEATH_NOUNS = {"гибель", "смерть", "жертва", "убийство", "погибший", "убитый",
               "теракт", "катастрофа", "некролог", "похороны", "самоубийство"}
# Слова, которые сами по себе означают людей, даже если морфология не подскажет
PEOPLE = {"человек", "люди", "пассажир", "ребёнок", "ребенок", "дети", "житель",
          "солдат", "военный", "военнослужащий", "рабочий", "водитель", "турист",
          "школьник", "студент", "мужчина", "женщина", "подросток", "спортсмен",
          "велогонщик", "пилот", "экипаж", "шахтёр", "шахтер", "боец", "мирный"}
# Обороты, где слова о смерти — метафора, а не событие
METAPHORS = re.compile(
    r"убийственн|мёртвый сезон|мертвый сезон|гибель урожая|смертельно скучн|"
    r"убить время|мёртвая точка|мертвая точка|смерть бренда|похороны проекта",
    re.I,
)

LEVEL_NONE = "none"
LEVEL_DEATH = "death"


@functools.lru_cache(maxsize=1)
def _nlp():
    from natasha import Doc, MorphVocab, NewsEmbedding, NewsMorphTagger, NewsSyntaxParser, Segmenter
    emb = NewsEmbedding()
    return {"seg": Segmenter(), "morph": NewsMorphTagger(emb),
            "syntax": NewsSyntaxParser(emb), "vocab": MorphVocab(), "Doc": Doc}


def classify(title: str, body: str) -> tuple[str, str]:
    """Вернуть (уровень, объяснение). Объяснение показывается пользователю."""
    text = f"{title}. {body}"
    if METAPHORS.search(text):
        # Метафору проверяем первой: «убийственный ценник» не должен блокировать иронию.
        if not any(re.search(rf"\b{v[:5]}", text, re.I) for v in ("погиб", "скончал", "жертв")):
            return LEVEL_NONE, ""

    nlp = _nlp()
    doc = nlp["Doc"](text[:4000])
    doc.segment(nlp["seg"])
    doc.tag_morph(nlp["morph"])
    doc.parse_syntax(nlp["syntax"])

    for token in doc.tokens:
        token.lemmatize(nlp["vocab"])

    by_id = {t.id: t for t in doc.tokens}
    for token in doc.tokens:
        lemma = (token.lemma or "").lower()
        is_verb = lemma in DEATH_VERBS
        is_noun = lemma in DEATH_NOUNS
        if not (is_verb or is_noun):
            continue

        # Кто именно: подлежащее глагола либо зависимое существительное в родительном
        related = [t for t in doc.tokens
                   if t.head_id == token.id and t.rel in ("nsubj", "nsubj:pass", "nmod", "obj")]
        # У «гибель X» зависимое может стоять и через предлог
        for t in list(related):
            related += [x for x in doc.tokens if x.head_id == t.id and x.rel == "nmod"]

        for dep in related:
            dep_lemma = (dep.lemma or "").lower()
            animate = "Animacy=Anim" in str(dep.feats or {}) or (
                dep.feats or {}).get("Animacy") == "Anim"
            if animate or dep_lemma in PEOPLE:
                who = dep.text
                return LEVEL_DEATH, f"сработала лемма «{lemma}» рядом со словом «{who}»"

        # Слова, которые почти всегда означают гибель людей и без уточнения
        if lemma in {"теракт", "некролог", "похороны", "самоубийство", "убийство"}:
            return LEVEL_DEATH, f"сработала лемма «{lemma}»"
        if is_verb and not related:
            head = by_id.get(token.head_id)
            if head and (head.lemma or "").lower() in PEOPLE:
                return LEVEL_DEATH, f"сработала лемма «{lemma}»"

    return LEVEL_NONE, ""


def mark_articles(verbose: bool = True) -> dict:
    """Разметить все статьи в базе."""
    from app.db import session

    counts = {LEVEL_NONE: 0, LEVEL_DEATH: 0}
    with session() as conn:
        rows = conn.execute(
            "SELECT id, title, rewrite_source_text FROM articles WHERE rewrite_source_text IS NOT NULL"
        ).fetchall()
        for row in rows:
            level, reason = classify(row["title"], row["rewrite_source_text"])
            counts[level] += 1
            conn.execute(
                "UPDATE articles SET sensitivity=?, sensitivity_reason=? WHERE id=?",
                (level, reason, row["id"]),
            )
    if verbose:
        print(f"размечено {len(rows)}: обычных {counts[LEVEL_NONE]}, "
              f"о гибели людей {counts[LEVEL_DEATH]}")
    return counts


def mood_allowed(mood: dict, sensitivity: str) -> bool:
    """Можно ли применять этот тон к материалу такой чувствительности."""
    allowed = mood.get("allowed_max_sensitivity", "none")
    if allowed == "any":
        return True
    return sensitivity == LEVEL_NONE
