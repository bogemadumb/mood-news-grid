"""Настройки приложения. Всё, что отличается между ноутбуком и сервером, живёт здесь."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Под каким путём приложение видно снаружи. Пусто — значит корень домена.
    root_path: str = ""
    db_path: Path = BASE_DIR / "data" / "news.db"
    git_sha: str = "dev"
    # Ниже этого числа готовых новостей витрина считается сломанной и /healthz отдаёт 503.
    # Шесть — столько нужно, чтобы грид выглядел лентой, а не заготовкой.
    # Требование ТЗ про «минимум 10 новостей» проверяется отдельно, по всей базе.
    min_ready_articles: int = 6
    min_articles_total: int = 10


settings = Settings()
