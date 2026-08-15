import re
from .fc import parse_doc, SPEECH, QUOTE_RE, norm_quote

def attribute_quotes(text):
    doc = parse_doc(text)
    tok_by_id = {t.id: t for t in doc.tokens}
    spans = doc.spans
    def span_of(tok):
        for s in spans:
            if s.start <= tok.start < s.stop and s.type in ('PER', 'ORG'):
                return s
        return None
    out = []
    for m in QUOTE_RE.finditer(text):
        sent = next((s for s in doc.sents if s.start <= m.start() < s.stop), None)
        if not sent: continue
        speaker, how = None, 'none'
        verbs = [t for t in sent.tokens if t.lemma in SPEECH and t.pos == 'VERB']
        for v in verbs:
            subj = [t for t in sent.tokens if t.head_id == v.id and t.rel in ('nsubj', 'nsubj:pass')]
            for s_tok in subj:
                sp = span_of(s_tok)
                if sp:
                    speaker, how = sp.normal, 'nsubj->span'; break
                # подлежащее — нарицательное («глава ведомства», «дипломат»): ищем аппозицию
                appos = [t for t in sent.tokens if t.head_id == s_tok.id and t.rel in ('appos', 'flat:name', 'nmod')]
                for a in appos:
                    sp2 = span_of(a)
                    if sp2:
                        speaker, how = sp2.normal, 'appos->span'; break
                if speaker: break
            if speaker: break
        if not speaker:
            cands = [s for s in spans if sent.start <= s.start < sent.stop and s.type in ('PER', 'ORG')]
            if cands:
                speaker = min(cands, key=lambda s: abs(s.start - m.start())).normal
                how = 'nearest'
        out.append((norm_quote(m.group(1))[:45], speaker, how))
    return out

TESTS = [
 'Глава ведомства Максим Решетников заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей».',
 '«Мы рассчитываем на инвестиции в 4,5 трлн рублей», — заявил глава ведомства Максим Решетников.',
 'Как сообщил журналистам Максим Решетников, ситуация улучшается. Он добавил: «Инвестиции вырастут на 20 процентов».',
 '«Я не могу понять, как прославление тех, кто принимал участие в преступлениях, помогает бороться с антисемитизмом», — написал дипломат Йоэль Лион.',
 'Эльвира Набиуллина заявила: «Мы рассчитываем на инвестиции в 4,5 трлн рублей».',
 'В Кремле отреагировали: «Решение принято», — сказал Дмитрий Песков.',
]
for t in TESTS:
    print(attribute_quotes(t), '|', t[:60])
