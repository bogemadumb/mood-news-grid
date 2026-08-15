"""Отбор витрины.

Показываем не всё, что собрали, а то, что имеет смысл показывать.

Окно 800–4000 знаков выбрано не на глаз. Пункт 5 ТЗ требует показывать исходный текст
рядом с переписанным; если оригинал обрезать, читатель увидит факты, которых слева нет,
и это выглядит как провал проверки, а не как экономия места. В это окно помещается вся
заметка целиком. Материалы длиннее остаются в базе, но карточками не становятся.
"""
from datetime import UTC, datetime

from app.db import session

MIN_LEN = 800
MAX_LEN = 4000
SHOWCASE_LIMIT = 30
MIN_PER_SOURCE_DIVERSITY = 2


def curate(limit: int = SHOWCASE_LIMIT, verbose: bool = True) -> dict:
    with session() as conn:
        conn.execute("UPDATE articles SET in_showcase=0")

        # Сначала по свежести, но с оглядкой на разнообразие изданий:
        # витрина из одного источника выглядит как ошибка сбора.
        rows = conn.execute(
            """SELECT a.id, s.code, a.published_at
               FROM articles a JOIN sources s ON s.id = a.source_id
               WHERE a.source_len BETWEEN ? AND ? AND a.status != 'failed'
               ORDER BY a.published_at DESC""",
            (MIN_LEN, MAX_LEN),
        ).fetchall()

        picked: list[int] = []
        per_source: dict[str, int] = {}

        # Первый проход: гарантируем по паре новостей от каждого издания.
        for row in rows:
            if len(picked) >= limit:
                break
            if per_source.get(row["code"], 0) < MIN_PER_SOURCE_DIVERSITY:
                picked.append(row["id"])
                per_source[row["code"]] = per_source.get(row["code"], 0) + 1

        # Второй проход: добираем самыми свежими.
        for row in rows:
            if len(picked) >= limit:
                break
            if row["id"] not in picked:
                picked.append(row["id"])
                per_source[row["code"]] = per_source.get(row["code"], 0) + 1

        if picked:
            marks = ",".join("?" * len(picked))
            conn.execute(f"UPDATE articles SET in_showcase=1 WHERE id IN ({marks})", picked)

        conn.execute(
            "INSERT INTO pipeline_runs (run_id, kind, started_at, finished_at, ok, items_out)"
            " VALUES (?,?,?,?,1,?)",
            ("curate", "curate", datetime.now(UTC).isoformat(),
             datetime.now(UTC).isoformat(), len(picked)),
        )

        if verbose:
            print(f"в витрине: {len(picked)} из {len(rows)} подходящих по длине")
            for code, count in sorted(per_source.items(), key=lambda x: -x[1]):
                print(f"  {code:<14} {count}")

        return {"showcase": len(picked), "eligible": len(rows), "per_source": per_source}
