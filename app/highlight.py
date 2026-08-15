"""Подсветка фактов в тексте.

ЕДИНСТВЕННОЕ место в проекте, где собирается HTML из данных. Больше нигде в шаблонах
нет `|safe`. Причина простая: заголовки и тексты приходят из интернета, то есть это
недоверенный ввод на публичном сайте. Если экранирование размазать по шаблонам,
рано или поздно найдётся ветка, где его забыли.

Здесь каждый кусок текста прогоняется через html.escape, и только сама функция
добавляет теги <mark>.
"""
import html
import re

from markupsafe import Markup


def render_spans(text: str, spans: list[dict]) -> Markup:
    """Разрезать текст по смещениям и обернуть найденные факты в <mark>.

    spans: [{"start": int, "end": int, "kind": str, "id": str, "status": str}, ...]
    Пересекающиеся отрезки отбрасываются: подсветить один и тот же кусок дважды нельзя.
    """
    if not text:
        return Markup("")
    if not spans:
        return Markup(html.escape(text))

    ordered = sorted((s for s in spans if 0 <= s["start"] < s["end"] <= len(text)),
                     key=lambda s: (s["start"], -s["end"]))
    out: list[str] = []
    cursor = 0
    for span in ordered:
        if span["start"] < cursor:
            continue
        out.append(html.escape(text[cursor:span["start"]]))
        fragment = html.escape(text[span["start"]:span["end"]])
        out.append(
            f'<mark class="fact fact-{html.escape(span.get("status", "ok"))}" '
            f'data-fact="{html.escape(str(span.get("id", "")))}" '
            f'title="{html.escape(span.get("kind", ""))}">{fragment}</mark>'
        )
        cursor = span["end"]
    out.append(html.escape(text[cursor:]))
    return Markup("".join(out))


def find_spans(text: str, surfaces: list[tuple[str, str, str]]) -> list[dict]:
    """Найти в тексте места, где встречается каждый факт.

    surfaces: [(fact_id, kind, surface), ...]. Берётся первое вхождение,
    поиск без учёта регистра. Ненайденные молча пропускаются — это нормально:
    в переписанном тексте факт может быть выражен другой формой слова,
    и его отсутствие уже зафиксировано отдельно, в результатах сверки.
    """
    spans: list[dict] = []
    used: list[tuple[int, int]] = []
    for fact_id, kind, surface in surfaces:
        if not surface or len(surface) < 2:
            continue
        for match in re.finditer(re.escape(surface), text, re.IGNORECASE):
            start, end = match.span()
            # Длинные цитаты приходят в отчёт обрезанными, и подсветка обрывалась
            # посреди слова («…беспилотными лета|тельными»). Дотягиваем до его конца.
            while end < len(text) and (text[end].isalnum() or text[end] in "-—"):
                end += 1
            if any(start < u_end and end > u_start for u_start, u_end in used):
                continue
            spans.append({"start": start, "end": end, "kind": kind, "id": fact_id, "status": "ok"})
            used.append((start, end))
            break
    return spans
