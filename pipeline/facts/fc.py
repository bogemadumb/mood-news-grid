"""Прототип детектора искажения фактов (RU). Проверка идей, не финальный код."""
import re
import unicodedata
from decimal import Decimal

import dateparser
from natasha import (
    DatesExtractor,
    Doc,
    MoneyExtractor,
    MorphVocab,
    NewsEmbedding,
    NewsMorphTagger,
    NewsNERTagger,
    NewsSyntaxParser,
    Segmenter,
)

SEG = Segmenter(); MV = MorphVocab(); EMB = NewsEmbedding()
MORPH = NewsMorphTagger(EMB); SYNTAX = NewsSyntaxParser(EMB); NER = NewsNERTagger(EMB)
DATES = DatesExtractor(MV); MONEY = MoneyExtractor(MV)

MULT = {'тыс': 1e3, 'тысяч': 1e3, 'тысячи': 1e3, 'тысяча': 1e3,
        'млн': 1e6, 'миллион': 1e6, 'миллиона': 1e6, 'миллионов': 1e6,
        'млрд': 1e9, 'миллиард': 1e9, 'миллиарда': 1e9, 'миллиардов': 1e9,
        'трлн': 1e12, 'триллион': 1e12, 'триллиона': 1e12, 'триллионов': 1e12}
CUR = {'руб': 'RUB', 'рубль': 'RUB', 'рубля': 'RUB', 'рублей': 'RUB', '₽': 'RUB',
       'долл': 'USD', 'доллар': 'USD', 'доллара': 'USD', 'долларов': 'USD', '$': 'USD',
       'евро': 'EUR', '€': 'EUR', 'юан': 'CNY', 'юаней': 'CNY', '¥': 'CNY'}
PCT = {'%', 'процент', 'процента', 'процентов', 'п.п.', 'процентных'}

NUM_RE = re.compile(
    r'(?P<approx>(?:не менее|не более|более|свыше|менее|почти|около|порядка|минимум|максимум)\s+)?'
    r'(?P<cur_pre>[$€₽¥])?\s*'
    r'(?P<num>\d{1,3}(?:[    ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)'
    r'(?:\s*(?P<mult>тыс\.?|млн|млрд|трлн|тысяч[аи]?|миллион[аов]*|миллиард[аов]*|триллион[аов]*))?'
    r'(?:\s*(?P<unit>%|процент(?:ов|а|ных)?|п\.п\.|рубл[а-я]+|руб\.?|доллар(?:ов|а)?|долл\.?|евро|юан[а-я]+|[$€₽¥]))?',
    re.IGNORECASE)

def norm_num(raw, mult):
    v = Decimal(re.sub(r'[    ]', '', raw).replace(',', '.'))
    if mult:
        key = mult.rstrip('.').lower()
        for k, m in MULT.items():
            if key.startswith(k[:4]):
                v *= Decimal(str(m)); break
    return v

def extract_numbers(text):
    out = []
    for m in NUM_RE.finditer(text):
        raw, mult, unit, cpre = m.group('num'), m.group('mult'), m.group('unit'), m.group('cur_pre')
        # пропускаем числа, которые уже съедены датой (обрабатываются отдельно)
        val = norm_num(raw, mult)
        kind, u = 'number', None
        if unit:
            ul = unit.rstrip('.').lower()
            if ul in PCT or ul.startswith('процент') or ul == '%':
                kind, u = 'percent', '%'
            elif ul in CUR or any(ul.startswith(k) for k in CUR):
                kind = 'money'
                u = next((c for k, c in CUR.items() if ul.startswith(k[:4])), None)
        if cpre and kind == 'number':
            kind, u = 'money', CUR.get(cpre)
        start = m.start('cur_pre') if cpre else m.start('num')  # без ведущего пробела!
        approx = (m.group('approx') or '').strip().lower() or None
        out.append(dict(kind=kind, surface=m.group(0).strip(), value=val, unit=u,
                        approx=approx, start=start, stop=m.end()))
    return out

REL_RE = re.compile(r'\b(вчера|позавчера|сегодня|завтра|послезавтра|на прошлой неделе|'
                    r'на этой неделе|в прошлом месяце|в прошлом году|в этом году|'
                    r'\d+ (?:дн[яей]+|недел[иьюя]+|месяц[аев]*|год[аов]*) назад)\b', re.IGNORECASE)

def extract_dates(text, published_at):
    out = []
    for m in DATES(text):
        f = m.fact.as_json
        out.append(dict(kind='date', surface=text[m.start:m.stop],
                        value=(f.get('year'), f.get('month'), f.get('day')),
                        start=m.start, stop=m.stop, relative=False))
    for m in REL_RE.finditer(text):
        d = dateparser.parse(m.group(0), languages=['ru'],
                             settings={'RELATIVE_BASE': published_at, 'PREFER_DATES_FROM': 'past'})
        if d:
            out.append(dict(kind='date', surface=m.group(0),
                            value=(d.year, d.month, d.day),
                            start=m.start(), stop=m.end(), relative=True))
    return out

QUOTE_RE = re.compile(r'[«"“]([^«»"“”]{15,})[»"”]')

def norm_quote(s):
    s = unicodedata.normalize('NFKC', s).lower()
    s = re.sub(r'[«»"“”\'`]', '', s)
    return re.sub(r'\s+', ' ', s).strip(' .,!?;:—-')

SPEECH = {'заявить', 'сказать', 'сообщить', 'отметить', 'подчеркнуть', 'добавить',
          'написать', 'рассказать', 'пояснить', 'признать', 'уточнить', 'объявить'}

def parse_doc(text):
    d = Doc(text)
    d.segment(SEG); d.tag_morph(MORPH); d.parse_syntax(SYNTAX); d.tag_ner(NER)
    for t in d.tokens: t.lemmatize(MV)
    for s in d.spans: s.normalize(MV)
    return d

def extract_entities(doc):
    out = []
    for s in doc.spans:
        lemmas = tuple(t.lemma for t in s.tokens if t.lemma)
        out.append(dict(kind=s.type.lower(), surface=s.text, normal=s.normal,
                        lemmas=lemmas, start=s.start, stop=s.stop))
    return out

def looks_like_speech(fragment, doc, start, stop):
    """Прямая ли это речь.

    В русском в «ёлочки» берут не только реплики, но и названия: газета «Украинская правда»,
    порт «Южный», компания «Яндекс». Считать их цитатами нельзя — потом проверка требует
    у названия газеты говорящего и блокирует корректный пересказ.

    Признак речи: внутри кавычек есть глагол и хотя бы четыре слова.
    """
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{2,}", fragment)
    if len(words) < 4:
        return False
    inner = [t for t in doc.tokens if start <= t.start < stop]
    return any(t.pos in ("VERB", "AUX") for t in inner)


def extract_quotes(doc, text):
    res = []
    for m in QUOTE_RE.finditer(text):
        q = m.group(1)
        is_speech = looks_like_speech(q, doc, m.start(1), m.end(1))
        speaker = None
        for sent in doc.sents:
            if sent.start <= m.start() < sent.stop:
                # ищем говорящего: PER/ORG в том же предложении рядом с глаголом речи
                has_speech = any(t.lemma in SPEECH for t in sent.tokens)
                cands = [s for s in doc.spans if sent.start <= s.start < sent.stop
                         and s.type in ('PER', 'ORG')]
                if cands:
                    speaker = min(cands, key=lambda s: min(abs(s.start - m.end()),
                                                           abs(m.start() - s.stop))).normal
                if not has_speech and not cands:
                    speaker = None
                break
        res.append(dict(kind='quote', surface=q, norm=norm_quote(q), is_speech=is_speech,
                        speaker=speaker if is_speech else None,
                        start=m.start(), stop=m.end()))
    return res

NEG = {'не', 'ни', 'нет', 'без', 'никогда', 'никто', 'ничего', 'нельзя', 'отказаться', 'отрицать'}
# ТОЛЬКО модальность/неуверенность. Глаголы речи (сообщать, писать, заявлять) сюда НЕЛЬЗЯ:
# их замена — законная стилистическая вариация, а не потеря факта.
HEDGE = {'мочь', 'возможно', 'вероятно', 'планировать', 'намереваться', 'ожидаться',
         'предполагать', 'рассчитывать', 'предположительно', 'якобы', 'рискнуть',
         'обсуждать', 'намерен', 'может'}
SPEECH_ATTR = {'заявить', 'сказать', 'сообщить', 'написать', 'передать', 'рассказать',
               'отметить', 'подчеркнуть', 'пояснить', 'уточнить', 'добавить', 'писать',
               'сообщать', 'заявлять', 'утверждать', 'данные', 'слово'}
BOOST = {'впервые', 'рекорд', 'рекордный', 'крупнейший', 'единственный', 'все', 'всегда',
         'никогда', 'полностью', 'абсолютно', 'катастрофа', 'провал', 'триумф', 'исторический'}
CAUSE = {'из-за', '因', 'вследствие', 'поэтому', 'потому', 'благодаря', 'привести', 'спровоцировать'}

def lemma_set(doc):
    return {t.lemma for t in doc.tokens if t.lemma}

def lemma_seq(doc):
    return [t.lemma for t in doc.tokens if t.lemma]

ALIAS = [
    {'сша', 'соединить штат америка', 'соединить штат', 'штаты', 'америка'},
    {'рф', 'россия', 'российский федерация'},
    {'цб', 'центробанк', 'банк россия', 'цб рф', 'центральный банк'},
    {'ес', 'евросоюз', 'европейский союз'},
    {'оон', 'организация объединить нация'},
    {'кнр', 'китай', 'китайский народный республика'},
    {'мвф', 'международный валютный фонд'},
]

def alias_key(s):
    return re.sub(r'\s+', ' ', s.lower().replace('«', '').replace('»', '')).strip()

def aliases_of(name_lemmas):
    key = alias_key(' '.join(name_lemmas))
    for grp in ALIAS:
        if key in grp:
            return grp
    return {key}

def contains_seq(hay, needle):
    n = len(needle)
    return any(hay[i:i + n] == list(needle) for i in range(len(hay) - n + 1))
