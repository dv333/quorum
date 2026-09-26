"""App settings from the environment."""

import os
from dataclasses import dataclass
from typing import Mapping, Optional

TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    db_url: str = "sqlite:///app.db"
    debug: bool = False
    workers: int = 4


def _flag(value: str) -> bool:
    return value.strip().lower() in TRUE_VALUES


def load_settings(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Read settings from the environment, or from the mapping given (for tests)."""
    env = os.environ if env is None else env
    return Settings(
        db_url=env.get("DB_URL", Settings.db_url),
        debug=_flag(env.get("DEBUG", "0")),
        workers=int(env.get("WORKERS", Settings.workers)),
    )


def get_settings() -> dict:
    """The settings as a dict, kept for existing callers."""
    s = load_settings()
    return {"db_url": s.db_url, "debug": s.debug, "workers": s.workers}
