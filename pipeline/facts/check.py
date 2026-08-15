import re

import pymorphy3

MORPH_PARSER = pymorphy3.MorphAnalyzer()
from decimal import Decimal

from rapidfuzz import fuzz

from .fc import (
    BOOST,
    HEDGE,
    aliases_of,
    contains_seq,
    extract_dates,
    extract_entities,
    extract_numbers,
    extract_quotes,
    lemma_seq,
    lemma_set,
    parse_doc,
)

NEG_PARTICLES = {'не', 'ни'}


def negated_verbs(doc) -> dict:
    """Глаголы с отрицанием: лемма глагола -> True, если перед ним стоит частица.

    Смотрим синтаксис: частица должна быть привязана именно к этому глаголу,
    а не просто стоять где-то в предложении.
    """
    negated, plain = set(), set()
    for sent in doc.sents:
        toks = list(sent.tokens)
        for i, t in enumerate(toks):
            if t.pos != 'VERB':
                continue
            has_neg = any(
                p.lemma in NEG_PARTICLES and (p.head_id == t.id or (0 <= i - 1 < len(toks) and toks[i - 1] is p))
                for p in toks
            )
            (negated if has_neg else plain).add(t.lemma)
    return {'negated': negated, 'plain': plain}


def negation_flips(O, N) -> list:
    """Сравнить отрицания по совпадающим сказуемым.

    Глаголы, которые в одном и том же тексте встречаются и с отрицанием, и без него,
    из сравнения исключаются: понять, какое из вхождений с каким сопоставлять, по множествам
    нельзя, а угадывать — значит выдумывать нарушение. Такие случаи уходят в известные дыры.
    """
    o, n = negated_verbs(O['doc']), negated_verbs(N['doc'])
    ambiguous = (o['negated'] & o['plain']) | (n['negated'] & n['plain'])
    flips = []
    for lemma in sorted((o['negated'] & n['plain']) - ambiguous):
        flips.append(('negation_dropped', lemma))
    for lemma in sorted((n['negated'] & o['plain']) - ambiguous):
        flips.append(('negation_added', lemma))
    return flips


def build_facts(text, published_at):
    doc = parse_doc(text)
    nums = extract_numbers(text)
    dates = extract_dates(text, published_at)
    # число внутри спана даты -> не отдельный факт
    dspans = [(d['start'], d['stop']) for d in dates]
    nums = [n for n in nums if not any(s <= n['start'] < e for s, e in dspans)]
    ents = extract_entities(doc)
    quotes = extract_quotes(doc, text)
    return dict(doc=doc, numbers=nums, dates=dates, entities=ents, quotes=quotes,
                lemmas=lemma_set(doc), lemseq=lemma_seq(doc), text=text)

APPROX_EQ = [{'более', 'свыше', 'не менее', 'минимум'}, {'менее', 'до', 'не более', 'максимум'},
             {'около', 'почти', 'порядка'}]

def same_approx(a, b):
    if (a or None) == (b or None):
        return True
    return any(a in g and b in g for g in APPROX_EQ if a and b)

ORDER = ['ok', 'approx_changed', 'unit_changed', 'rounded', 'missing']

def match_number(f, target):
    """Берём ЛУЧШЕГО кандидата, а не первого: в тексте одно и то же число
    может встретиться дважды («300 с лишним» и «более 300»)."""
    best = ('missing', None)
    for c in target['numbers']:
        if c['value'] == f['value'] and (f['unit'] or None) == (c['unit'] or None):
            st = 'ok' if same_approx(f.get('approx'), c.get('approx')) else 'approx_changed'
        elif c['value'] == f['value']:
            st = 'unit_changed'
        elif f['value'] and abs(c['value'] - f['value']) / abs(f['value']) < Decimal('0.01'):
            st = 'rounded'
        else:
            continue
        if ORDER.index(st) < ORDER.index(best[0]):
            best = (st, c['surface'])
    return best

def match_date(f, target):
    for c in target['dates']:
        if c['value'] == f['value']:
            return 'ok', c['surface']
    for c in target['dates']:
        # частичное совпадение: год+месяц те же, день потерян
        if c['value'][:2] == f['value'][:2] and (c['value'][2] is None) != (f['value'][2] is None):
            return 'partial', c['surface']
    return 'missing', None

def match_entity(f, target):
    tl = target['lemseq']
    if contains_seq(tl, f['lemmas']):
        return 'ok', f['surface']
    my = aliases_of(f['lemmas'])
    for c in target['entities']:
        if aliases_of(c['lemmas']) & my:
            return 'alias', c['surface']
    if f['kind'] == 'per' and len(f['lemmas']) > 1:
        surname = f['lemmas'][-1]
        if surname in target['lemmas']:
            return 'short', surname
    best = max(((fuzz.token_sort_ratio(f['normal'].lower(), c['normal'].lower()), c)
                for c in target['entities']), key=lambda p: p[0], default=(0, None))
    if best[0] >= 90:
        return 'fuzzy', best[1]['surface']
    return 'missing', None

def _speaker_lemmas(name: str) -> frozenset:
    """Имя спикера в леммах.

    natasha возвращает атрибуцию в том падеже, в каком она стоит в предложении:
    «по словам Санчеса» -> «Санчеса», «Санчес заявил» -> «Санчес». Это один человек,
    и сравнивать такие строки напрямую нельзя.
    """
    if not name:
        return frozenset()
    forms = set()
    for w in re.findall(r"[А-Яа-яЁёA-Za-z-]{2,}", name):
        low = w.lower()
        forms.add(low)
        # Морфология угадывает иностранные фамилии неверно: «Аюсо» -> «аюсый».
        # Поэтому в сравнение идут обе формы — и лемма, и само слово.
        forms.add(MORPH_PARSER.parse(w)[0].normal_form)
    return frozenset(forms)


def same_speaker(a: str, b: str) -> bool:
    """Один ли это человек. Допускается сокращение до фамилии: «Педро Санчес» и «Санчес»."""
    la, lb = _speaker_lemmas(a), _speaker_lemmas(b)
    if not la and not lb:
        return True
    if not la or not lb:
        return False
    return bool(la & lb)


# Названия площадок, куда упирается атрибуция: «разместила на своей странице в соцсети X»
# -> разбор считает говорящим саму соцсеть. Поймано на живой новости про Аюсо.
PLATFORMS = {'x', 'twitter', 'telegram', 'телеграм', 'facebook', 'instagram', 'вконтакте', 'вк',
             'youtube', 'tiktok', 'threads', 'соцсеть', 'соцсети', 'макс', 'max', 'whatsapp',
             'дзен', 'rutube', 'одноклассники'}


def is_platform(surface) -> bool:
    """Название площадки публикации, а не участник события."""
    if not surface:
        return False
    words = re.findall(r"[А-Яа-яЁёA-Za-z]{2,}", str(surface))
    return any(MORPH_PARSER.parse(w)[0].normal_form in PLATFORMS or w.lower() in PLATFORMS
               for w in words)


def plausible_speaker(name) -> bool:
    """Похоже ли это на говорящего вообще.

    Отсекаются однобуквенные обрывки и названия площадок публикации.
    Если атрибуция мусорная, честнее считать её неизвестной, чем сравнивать мусор с именем.
    """
    if not name:
        return False
    clean = str(name).strip()
    return len(clean) >= 3 and clean.lower() not in PLATFORMS


def compare_speakers(orig_speaker, new_speaker, orig_facts) -> str:
    """Вернуть 'ok', 'speaker_changed' или 'speaker_lost'.

    Четыре ситуации, и только одна из них — настоящее нарушение:
      оба известны и разные            -> подмена говорящего, нарушение;
      оба известны и совпадают         -> норма (с поправкой на падежи);
      в оригинале мусор или пусто      -> сравнивать не с чем. Если в пересказе появилось имя,
                                          оно должно встречаться в оригинале — иначе выдумка;
      в оригинале есть, в пересказе нет -> атрибуция потеряна, помечаем мягко.
    """
    o_ok, n_ok = plausible_speaker(orig_speaker), plausible_speaker(new_speaker)
    if o_ok and n_ok:
        return 'ok' if same_speaker(orig_speaker, new_speaker) else 'speaker_changed'
    if o_ok and not n_ok:
        return 'speaker_lost'
    if not o_ok and n_ok:
        # Модель могла восстановить говорящего из контекста статьи — это не выдумка,
        # если имя есть в оригинале. Если имени нет — выдумала.
        lemmas = _speaker_lemmas(new_speaker)
        return 'ok' if lemmas & set(orig_facts['lemmas']) else 'speaker_changed'
    return 'ok'


def match_quote(f, target, orig_facts=None):
    for c in target['quotes']:
        if c['norm'] == f['norm']:
            # У закавыченного названия говорящего нет и быть не может: сверяем только наличие.
            if not (f.get('is_speech') and c.get('is_speech')):
                return 'ok', c['surface']
            st = compare_speakers(f.get('speaker'), c.get('speaker'),
                                  orig_facts or {'lemmas': set()})
            return st, c['surface']
    for c in target['quotes']:
        if fuzz.ratio(f['norm'], c['norm']) >= 92:
            return 'paraphrased', c['surface']
    return 'missing', None

SEVERITY = {'ok': 0, 'approx_changed': 2, 'alias': 0, 'short': 0, 'fuzzy': 1, 'rounded': 1, 'partial': 1,
            'unit_changed': 2, 'speaker_changed': 2, 'speaker_lost': 1, 'paraphrased': 2, 'missing': 2,
            'platform_dropped': 1}
WEIGHT = {'number': 1.0, 'money': 1.0, 'percent': 1.0, 'date': 1.0,
          'per': 1.0, 'org': 0.8, 'loc': 0.6, 'quote': 1.2}

def check(orig_text, new_text, published_at):
    O = build_facts(orig_text, published_at)
    N = build_facts(new_text, published_at)
    rows = []
    for f in O['numbers']:
        st, got = match_number(f, N); rows.append((f['kind'], f['surface'], st, got))
    for f in O['dates']:
        st, got = match_date(f, N); rows.append(('date', f['surface'], st, got))
    for f in O['entities']:
        st, got = match_entity(f, N); rows.append((f['kind'], f['surface'], st, got))
    for f in O['quotes']:
        st, got = match_quote(f, N, O); rows.append(('quote', f['surface'][:40], st, got))
    # обратная проверка: выдуманные числа/даты/сущности, которых нет в оригинале
    halluc = []
    for f in N['numbers']:
        if match_number(f, O)[0] in ('missing',):
            halluc.append(('number+', f['surface']))
    for f in N['dates']:
        if match_date(f, O)[0] == 'missing':
            halluc.append(('date+', f['surface']))
    for f in N['entities']:
        if match_entity(f, O)[0] == 'missing':
            halluc.append((f['kind'] + '+', f['surface']))
    # Отрицание: сравниваем не количество частиц, а привязку к сказуемому.
    #
    # Глобальный счётчик «не» здесь не работает, и это проверено на живых текстах:
    # у ироничного тона отрицание — родная конструкция («решила не отвлекаться»,
    # «это уже не сенсация»). В оригинале спортивной новости было ноль отрицаний,
    # в переписанном три, при полностью сохранных фактах — и корректный текст
    # браковался трижды подряд.
    #
    # Опасны два случая, оба про смену смысла у ОДНОГО И ТОГО ЖЕ глагола:
    #   1) отрицание пропало: «не смогли договориться» -> «смогли договориться»;
    #   2) отрицание появилось у глагола, который в оригинале шёл без него.
    # Отрицание при новом глаголе, которого в оригинале не было, — стилистика.
    neg_flips = negation_flips(O, N)
    hedge_lost = sorted((HEDGE & O['lemmas']) - N['lemmas'])
    boost_added = sorted((BOOST & N['lemmas']) - O['lemmas'])
    # Площадка, на которой сделано заявление («сообщил в своём канале в „Максе“»),
    # это канал публикации, а не суть новости. Её пропажа — замечание, а не блокировка.
    # Подмена одной организации другой при этом по-прежнему ловится: выброшенное название
    # даёт missing, а подставленное — запись в галлюцинациях.
    rows = [
        (kind, surface, 'platform_dropped' if (kind == 'org' and status == 'missing'
                                               and is_platform(surface)) else status, got)
        for kind, surface, status, got in rows
    ]
    halluc = [(k, s_) for k, s_ in halluc if not (k == 'org+' and is_platform(s_))]

    hard = [r for r in rows if SEVERITY[r[2]] == 2]
    soft = [r for r in rows if SEVERITY[r[2]] == 1]
    total_w = sum(WEIGHT.get(r[0], 1.0) for r in rows) or 1
    lost_w = sum(WEIGHT.get(r[0], 1.0) for r in hard) + 0.5 * sum(WEIGHT.get(r[0], 1.0) for r in soft)
    fpi = round(100 * (1 - lost_w / total_w), 1)
    return dict(rows=rows, hard=hard, soft=soft, halluc=halluc, fpi=fpi,
                neg_flips=neg_flips, neg_delta=len(neg_flips), hedge_lost=hedge_lost, boost_added=boost_added)

def verdict(r):
    bad = bool(r['hard']) or bool(r['halluc']) or bool(r['neg_flips']) or bool(r['hedge_lost']) or bool(r['boost_added'])
    return 'FAIL' if bad else ('WARN' if r['soft'] else 'PASS')
