"""Cross-platform locations for persistent THUFood files."""

import os
from pathlib import Path

from platformdirs import user_config_path, user_data_path


APP_NAME = "THUFood"


def _override(name):
    value = os.getenv(name, "").strip()
    return Path(value).expanduser().resolve() if value else None


def config_dir():
    """Small, non-secret settings which may roam with the OS user profile."""
    return _override("THUFOOD_CONFIG_DIR") or user_config_path(
        APP_NAME, appauthor=False, roaming=True,
    )


def data_dir():
    """Potentially large local user data which should not roam by default."""
    return _override("THUFOOD_DATA_DIR") or user_data_path(
        APP_NAME, appauthor=False, roaming=False,
    )


def records_dir():
    return data_dir() / "eat_records"
