"""Нормализация записей ленты: даты, ссылки, отпечатки, чистка HTML.

Отдельный файл, потому что от корректности этих мелочей зависит дедупликация:
одна и та же новость приходит с utm-метками, с www и без, с амперсандами в сущностях.
"""
import hashlib
import html
import re
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit

# Метки, которые не меняют содержимое страницы, но ломают сравнение ссылок.
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "yclid", "gclid", "fbclid", "ysclid", "erid", "_openstat", "from", "referrer",
}

FOOTER_MARKERS = re.compile(
    r"все права защищены|свидетельство о регистрации|при использовании материалов|"
    r"ссылка на .{0,25}обязательна|copyright ©|18\+|материал скопирован",
    re.I,
)


def strip_html(raw) -> str:
    # feedparser отдаёт часть полей то строкой, то словарём с ключом value,
    # то списком таких словарей — зависит от того, как их разметило конкретное издание.
    if not raw:
        return ""
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("name") or ""
    elif isinstance(raw, list):
        raw = " ".join(strip_html(item) for item in raw)
    if not isinstance(raw, str):
        raw = str(raw)
    if not raw:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def canonical_url(url: str) -> str:
    """Убрать всё, что не влияет на содержимое: схему, www, метки, хвостовой слэш."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    for prefix in ("www.", "m.", "amp."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    query_str = "&".join(f"{k}={v}" for k, v in sorted(query))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, query_str, ""))


def content_hash(title: str, body: str) -> str:
    norm = re.sub(r"\s+", " ", f"{title} {body}").strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def to_iso_utc(struct_time) -> str | None:
    """RFC 822 из ленты -> ISO-8601 в UTC. Без единого формата даты сортировка врёт."""
    if not struct_time:
        return None
    try:
        dt = datetime(*struct_time[:6], tzinfo=UTC)
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w-]+\b", text, flags=re.UNICODE))


def looks_like_footer(text: str) -> bool:
    """Юридический подвал сайта выглядит как валидная статья — ловим по маркерам."""
    return bool(FOOTER_MARKERS.search(text[:600]))


def long_sentence_ratio(text: str) -> float:
    """Доля предложений длиннее 40 знаков. У навигации и подвала она низкая."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return 0.0
    return sum(1 for s in sentences if len(s) > 40) / len(sentences)

# Рекламные хвосты изданий. Это не часть новости: модель их справедливо выбрасывает,
# а проверка засчитывала «РБК» и «Макс» как утраченные факты и браковала корректный пересказ.
PROMO_TAIL = re.compile(
    r"(?:^|\n|(?<=[.!?»])\s)"
    r"(?:"
    r"оставайтесь на связи[^\n]{0,80}"
    r"|подпис(?:ывайтесь|ка)[^\n]{0,90}"
    r"|следите за (?:нами|новостями)[^\n]{0,80}"
    r"|больше (?:новостей|материалов)[^\n]{0,80}"
    r"|читайте (?:также|нас)[^\n]{0,80}"
    r"|наш(?:и|его)? (?:телеграм|telegram|канал)[^\n]{0,80}"
    r"|присоединяйтесь[^\n]{0,80}"
    r")\s*$",
    re.I | re.M,
)


def strip_promo_tail(text: str) -> str:
    """Срезать рекламный хвост издания в конце заметки.

    Режем только с конца и только по явным маркерам: «Оставайтесь на связи с РБК в „Максе“»,
    «Подписывайтесь на наш телеграм-канал» и подобное. В середине текста такие фразы
    могут быть частью новости, поэтому там не трогаем.
    """
    if not text:
        return text
    cleaned = text
    for _ in range(3):  # хвостов может быть несколько подряд
        new = PROMO_TAIL.sub("", cleaned).rstrip()
        if new == cleaned:
            break
        cleaned = new
    return cleaned
