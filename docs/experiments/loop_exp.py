"""Цикл генерация -> проверка -> перегенерация на реальной локальной модели."""
import json, re, subprocess, time, datetime
from rapidfuzz import fuzz
from check import check, verdict

def style_distance(a, b):
    return round(1 - fuzz.ratio(a, b) / 100, 3)

PUB = datetime.datetime(2026, 8, 15)
ORIG = ('Минэкономразвития 12 марта 2026 года повысило прогноз роста ВВП России до 2,3% с 1,8%. '
        'Глава ведомства Максим Решетников заявил: «Мы рассчитываем на инвестиции в 4,5 трлн рублей». '
        'Вчера ЦБ РФ снизил ставку на 100 базисных пунктов, до 15% годовых. '
        'Компания «Яндекс» может вложить 250 млн долларов в дата-центр в Санкт-Петербурге до конца 2027 года. '
        'США и КНР не смогли договориться, сообщает Reuters.')

def llm(prompt, temp):
    body = json.dumps({'model': 'qwen3.5:4b', 'prompt': prompt, 'stream': False, 'think': False,
                       'options': {'temperature': temp, 'num_predict': 1200}})
    r = subprocess.run(['curl', '-s', '--max-time', '240', 'http://127.0.0.1:11434/api/generate',
                        '-H', 'Content-Type: application/json', '-d', body], capture_output=True)
    return json.loads(r.stdout.decode())['response'].strip()

BASE = ('Перепиши новость {mood}. Менять факты нельзя: числа, проценты, суммы, даты, имена, '
        'названия организаций, города и цитаты в кавычках переноси без изменений. '
        'Не добавляй фактов, которых нет в тексте, не убирай слова «может», «планирует», «не». '
        'Верни только переписанный текст.\n\nНовость:\n{text}')

REPAIR = ('Ты переписал новость, но автоматическая проверка нашла нарушения фактов.\n'
          'ОРИГИНАЛ:\n{orig}\n\nТВОЙ ВАРИАНТ:\n{new}\n\nНАРУШЕНИЯ:\n{viol}\n\n'
          'Исправь ТОЛЬКО эти нарушения, сохранив настроение «{mood}». '
          'Верни только исправленный текст.')

def violations_text(r):
    out = []
    for kind, surf, st, got in r['hard']:
        out.append(f'- {kind} «{surf}»: {st}' + (f' (в тексте стало «{got}»)' if got else ' (пропало)'))
    for k, s in r['halluc']:
        out.append(f'- в тексте появился факт, которого нет в оригинале: {k} «{s}»')
    if r['neg_delta']:
        out.append(f'- изменилось число отрицаний (Δ={r["neg_delta"]}): проверь частицу «не»')
    for h in r['hedge_lost']:
        out.append(f'- потеряна осторожная формулировка «{h}» — верни её')
    for b in r['boost_added']:
        out.append(f'- добавлено усиливающее слово «{b}», которого нет в оригинале — убери')
    return '\n'.join(out)

TEMPS = [0.8, 0.4, 0.2]
for mood in ['радостно', 'грустно, меланхолично', 'иронично, с сарказмом', 'нейтрально, сухо']:
    text = None
    for att, temp in enumerate(TEMPS, 1):
        t0 = time.time()
        if att == 1:
            prompt = BASE.format(mood=mood, text=ORIG)
        elif v == 'COPY':
            prompt = (BASE.format(mood=mood, text=ORIG) +
                      '\n\nВАЖНО: прошлый вариант почти дословно повторял оригинал. '
                      'Перепиши сильнее: смени порядок предложений, глаголы, добавь эмоциональные обороты. '
                      'Факты (числа, даты, имена, цитаты) при этом оставь ровно теми же.')
        else:
            prompt = REPAIR.format(orig=ORIG, new=text, viol=violations_text(r), mood=mood)
        text = llm(prompt, temp)
        r = check(ORIG, text, PUB)
        v = verdict(r)
        dist = style_distance(ORIG, text)
        if v == 'PASS' and dist < 0.15:
            v = 'COPY'   # факты целы, но текст почти не изменён — это не переписывание
        print(f'[{mood[:12]}] попытка {att} (t={temp}): {v} FPI={r["fpi"]} dist={dist} '
              f'hard={len(r["hard"])} halluc={len(r["halluc"])} {round(time.time()-t0,1)}s')
        if v == 'COPY':
            print('   - текст почти совпадает с оригиналом (дистанция %.2f < 0.15): настроение не применено' % dist)
        elif v != 'PASS':
            print('   ' + violations_text(r).replace('\n', '\n   '))
        if v == 'PASS':
            break
    print('   ИТОГ:', v, '| текст:', text[:160].replace('\n', ' '), '\n')
