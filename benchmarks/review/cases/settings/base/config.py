"""App settings from the environment."""

import os


def get_settings():
    return {
        "db_url": os.environ.get("DB_URL", "sqlite:///app.db"),
        "debug": os.environ.get("DEBUG", "0") == "1",
        "workers": int(os.environ.get("WORKERS", "4")),
    }
