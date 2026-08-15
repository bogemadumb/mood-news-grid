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
    # Минимум готовых статей, ниже которого /healthz честно отдаёт 503.
    min_ready_articles: int = 10


settings = Settings()
