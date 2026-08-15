import torch, re, time
from transformers import AutoTokenizer, AutoModelForSequenceClassification
from rapidfuzz import fuzz
M='cointegrated/rubert-base-cased-nli-threeway'
tok=AutoTokenizer.from_pretrained(M); mdl=AutoModelForSequenceClassification.from_pretrained(M); mdl.eval()
ID=mdl.config.id2label

ORIG=('Минэкономразвития 12 марта 2026 года повысило прогноз роста ВВП России до 2,3% с 1,8%. '
      'Глава ведомства Максим Решетников заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей». '
      'Вчера ЦБ РФ снизил ставку на 100 базисных пунктов, до 15% годовых. '
      'Компания «Яндекс» может вложить 250 млн долларов в дата-центр в Санкт-Петербурге до конца 2027 года. '
      'США и КНР не смогли договориться, сообщает Reuters.')
def sents(t): return [s.strip() for s in re.split(r'(?<=[.!?])\s+', t) if len(s.strip())>15]

def contradiction_scan(orig, new):
    O=sents(orig); res=[]
    with torch.inference_mode():
        for h in sents(new):
            prem=max(O, key=lambda p: fuzz.token_set_ratio(p,h))
            x=tok(prem,h,return_tensors='pt',truncation=True,max_length=512)
            p=torch.softmax(mdl(**x).logits,-1)[0]
            sc={ID[i]:float(p[i]) for i in range(len(p))}
            res.append((round(sc['contradiction'],3), round(sc['entailment'],3), h[:60], prem[:40]))
    return res

CASES={
 'честное радостное': ('Отличные новости! Минэкономразвития 12 марта 2026 года подняло прогноз роста ВВП России до 2,3% против прежних 1,8%. '
   'Глава ведомства Максим Решетников с воодушевлением заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей». '
   '14 августа 2026 года ЦБ РФ порадовал рынок, снизив ставку на 100 базисных пунктов, до 15% годовых. '
   'Компания «Яндекс» может вложить 250 млн долларов в дата-центр в Санкт-Петербурге до конца 2027 года. '
   'США и КНР пока не смогли договориться, сообщает Reuters.'),
 'иронично (сильная стилизация)': ('Гениально: Минэкономразвития 12 марта 2026 года расщедрилось и подняло прогноз роста ВВП России аж до 2,3% с 1,8%. '
   'Глава ведомства Максим Решетников бодро заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей». '
   'А ЦБ РФ 14 августа 2026 года снизил ставку на целых 100 базисных пунктов — до скромных 15% годовых. '
   '«Яндекс» может расстаться с 250 млн долларов ради дата-центра в Санкт-Петербурге до конца 2027 года. '
   'США и КНР договориться не сумели, сообщает Reuters.'),
 'MUT отрицание снято': ORIG.replace('не смогли договориться','смогли договориться'),
 'MUT модальность': ORIG.replace('может вложить','вложит'),
 'MUT причинность добавлена': ORIG.replace('снизил ставку','из-за замедления инфляции снизил ставку'),
}
for name, txt in CASES.items():
    print('===', name)
    for c,e,h,p in contradiction_scan(ORIG, txt):
        flag='CONTRA' if c>0.5 else ('     ' )
        print(f'  {flag} contra={c:<6} entail={e:<6} | {h}')
