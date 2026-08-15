"""Эксперимент: маскирование фактов плейсхолдерами против обычного промпта.
Локальная модель qwen3.5:4b через Ollama."""
import json, re, subprocess, time, datetime
from check import check, verdict, build_facts

PUB = datetime.datetime(2026, 8, 15)
ORIG = ('Минэкономразвития 12 марта 2026 года повысило прогноз роста ВВП России до 2,3% с 1,8%. '
        'Глава ведомства Максим Решетников заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей». '
        'Вчера ЦБ РФ снизил ставку на 100 базисных пунктов, до 15% годовых. '
        'Компания «Яндекс» может вложить 250 млн долларов в дата-центр в Санкт-Петербурге до конца 2027 года. '
        'США и КНР не смогли договориться, сообщает Reuters.')

def llm(prompt, temp=0.7):
    body = json.dumps({'model': 'qwen3.5:4b', 'prompt': prompt, 'stream': False, 'think': False,
                       'options': {'temperature': temp, 'num_predict': 1200}})
    r = subprocess.run(['curl', '-s', '--max-time', '240', 'http://127.0.0.1:11434/api/generate',
                        '-H', 'Content-Type: application/json', '-d', body], capture_output=True)
    out = json.loads(r.stdout.decode())['response']
    return re.sub(r'<think>.*?</think>', '', out, flags=re.S).strip()

def mask(text):
    F = build_facts(text, PUB)
    spans = []
    for i, f in enumerate(F['numbers']): spans.append((f['start'], f['stop'], f'{{N{i}}}', text[f['start']:f['stop']]))
    for i, f in enumerate(F['dates']):   spans.append((f['start'], f['stop'], f'{{D{i}}}', text[f['start']:f['stop']]))
    for i, f in enumerate(F['quotes']):  spans.append((f['start'], f['stop'], f'{{Q{i}}}', text[f['start']:f['stop']]))
    spans.sort(key=lambda s: s[0])
    keep, last = [], -1
    for s in spans:                      # выбрасываем пересечения
        if s[0] >= last: keep.append(s); last = s[1]
    out, prev, table = [], 0, {}
    for st, sp, ph, orig in keep:
        out.append(text[prev:st]); out.append(ph); table[ph] = orig; prev = sp
    out.append(text[prev:])
    return ''.join(out), table

def unmask(text, table):
    missing = [ph for ph in table if ph not in text]
    for ph, val in table.items():
        text = text.replace(ph, val)
    return text, missing

MOODS = {'радостно': 'радостно, приподнято, с воодушевлением',
         'грустно': 'грустно, меланхолично, с сожалением',
         'иронично': 'иронично, с сарказмом'}

P_PLAIN = ('Перепиши новость {mood}. Категорически запрещено менять факты: числа, даты, имена, '
           'названия, суммы, проценты и цитаты должны остаться ровно теми же. Ничего не добавляй от себя. '
           'Верни только текст.\n\nНовость:\n{text}')

P_MASK = ('Перепиши новость {mood}. В тексте есть метки вида {{N0}}, {{D1}}, {{Q0}} — это факты. '
          'Каждую метку нужно перенести в новый текст ровно один раз, символ в символ, не раскрывая и не меняя. '
          'Новые числа, даты и имена не добавляй. Верни только текст.\n\nНовость с метками:\n{text}')

def run():
    masked, table = mask(ORIG)
    print('--- текст с метками ---'); print(masked); print('--- таблица ---'); print(table); print()
    rows = []
    for mood, desc in MOODS.items():
        for mode in ('plain', 'mask'):
            t0 = time.time()
            if mode == 'plain':
                new = llm(P_PLAIN.format(mood=desc, text=ORIG))
                lost = []
            else:
                raw = llm(P_MASK.format(mood=desc, text=masked))
                new, lost = unmask(raw, table)
            r = check(ORIG, new, PUB)
            v = verdict(r)
            rows.append((mood, mode, v, r['fpi'], len(r['hard']), len(r['halluc']), lost, round(time.time()-t0, 1)))
            print(f'[{mood}/{mode}] {v} FPI={r["fpi"]} hard={len(r["hard"])} halluc={len(r["halluc"])} '
                  f'потеряно_меток={lost} {round(time.time()-t0,1)}s')
            for row in r['hard']: print('     hard:', row)
            if r['halluc']: print('     halluc:', r['halluc'])
            if r['hedge_lost']: print('     hedge_lost:', r['hedge_lost'])
            if r['boost_added']: print('     boost_added:', r['boost_added'])
            print('     ТЕКСТ:', new[:300].replace('\n', ' '))
    print()
    for r in rows: print(r)

run()
