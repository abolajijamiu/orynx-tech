"""Runtime settings, loaded from environment or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent
RECIPE_DIR = PACKAGE_ROOT / "recipes"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ORYNX_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "sqlite+pysqlite:///./orynx.db"

    # A crawler that does not say who it is cannot be contacted by a site owner
    # who wants it to stop, so the default carries a placeholder you must replace.
    user_agent: str = "OrynxBookLeads/0.1 (+https://example.com/bot)"
    contact_email: str = ""

    default_rate_limit_rps: float = 0.5
    default_concurrency: int = 4
    request_timeout: float = 30.0
    max_retries: int = 3
    obey_robots: bool = True

    cache_dir: Path = Path("./.cache/http")
    cache_ttl_hours: int = 168

    google_books_api_key: str = ""

    recipe_dir: Path = Field(default=RECIPE_DIR)
    export_dir: Path = Path("./exports")

    @property
    def cache_ttl_seconds(self) -> int:
        return self.cache_ttl_hours * 3600


@lru_cache
def get_settings() -> Settings:
    return Settings()
