"""Подключение к SQLite и накат миграций.

База — один файл. Его видно, можно скачать и открыть чем угодно.
Миграции накатываются по порядку номеров, версия лежит в PRAGMA user_version.
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import BASE_DIR, settings

MIGRATIONS_DIR = BASE_DIR / "migrations"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session(db_path: Path | None = None):
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def migrate(db_path: Path | None = None) -> int:
    """Накатить все миграции, которых ещё нет. Повторный запуск ничего не ломает."""
    with session(db_path) as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        applied = 0
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(sql_file.name.split("_")[0])
            if version <= current:
                continue
            conn.executescript(sql_file.read_text(encoding="utf-8"))
            applied += 1
        return applied
