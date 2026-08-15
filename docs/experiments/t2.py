from natasha import MorphVocab, MoneyExtractor, DatesExtractor
mv=MorphVocab(); money=MoneyExtractor(mv); dates=DatesExtractor(mv)
cases=['5 рублей','1000 рублей','1,5 млн рублей','4,5 трлн рублей','$250 млн','250 млн долларов',
       '10 миллионов рублей','100 тысяч долларов США','€40 млрд','сумма составила 3 млрд руб.',
       'зарплата 50 тыс. руб.','2 000 000 рублей']
for c in cases:
    r=[(c[m.start:m.stop], m.fact.as_json) for m in money(c)]
    print(repr(c),'->',r)
print('---dates---')
dcases=['вчера','сегодня','11 декабря','в понедельник','1 января 1909 года','в 2010 году',
        '12.03.2026','с 1 по 5 марта','в марте','на прошлой неделе','31 декабря 2025 г.']
for c in dcases:
    r=[(c[m.start:m.stop], dict(m.fact.as_json)) for m in dates(c)]
    print(repr(c),'->',r)
