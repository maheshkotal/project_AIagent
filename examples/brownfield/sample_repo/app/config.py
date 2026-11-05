"""Runtime configuration from environment variables."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    base_url: str


def load_settings(database_url: str | None = None) -> Settings:
    """Read settings from the environment; an explicit `database_url` wins."""
    return Settings(
        database_url=database_url or os.getenv("DATABASE_URL", "sqlite:///./data.db"),
        base_url=os.getenv("BASE_URL", "http://localhost:8000").rstrip("/"),
    )
