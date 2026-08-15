import datetime, re
from decimal import Decimal
from .fc import (parse_doc, extract_numbers, extract_dates, extract_entities, extract_quotes,
                lemma_seq, lemma_set, aliases_of, alias_key, contains_seq,
                NEG, HEDGE, BOOST, norm_quote)
from rapidfuzz import fuzz

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

def match_quote(f, target):
    for c in target['quotes']:
        if c['norm'] == f['norm']:
            st = 'ok' if (f['speaker'] or '') == (c['speaker'] or '') else 'speaker_changed'
            return st, c['surface']
    for c in target['quotes']:
        if fuzz.ratio(f['norm'], c['norm']) >= 92:
            return 'paraphrased', c['surface']
    return 'missing', None

SEVERITY = {'ok': 0, 'approx_changed': 2, 'alias': 0, 'short': 0, 'fuzzy': 1, 'rounded': 1, 'partial': 1,
            'unit_changed': 2, 'speaker_changed': 2, 'paraphrased': 2, 'missing': 2}
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
        st, got = match_quote(f, N); rows.append(('quote', f['surface'][:40], st, got))
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
    # отрицания и модальность на уровне документа
    neg_o = sum(1 for l in O['lemseq'] if l in NEG)
    neg_n = sum(1 for l in N['lemseq'] if l in NEG)
    hedge_lost = sorted((HEDGE & O['lemmas']) - N['lemmas'])
    boost_added = sorted((BOOST & N['lemmas']) - O['lemmas'])
    hard = [r for r in rows if SEVERITY[r[2]] == 2]
    soft = [r for r in rows if SEVERITY[r[2]] == 1]
    total_w = sum(WEIGHT.get(r[0], 1.0) for r in rows) or 1
    lost_w = sum(WEIGHT.get(r[0], 1.0) for r in hard) + 0.5 * sum(WEIGHT.get(r[0], 1.0) for r in soft)
    fpi = round(100 * (1 - lost_w / total_w), 1)
    return dict(rows=rows, hard=hard, soft=soft, halluc=halluc, fpi=fpi,
                neg_delta=neg_n - neg_o, hedge_lost=hedge_lost, boost_added=boost_added)

def verdict(r):
    bad = bool(r['hard']) or bool(r['halluc']) or r['neg_delta'] != 0 or bool(r['hedge_lost']) or bool(r['boost_added'])
    return 'FAIL' if bad else ('WARN' if r['soft'] else 'PASS')
