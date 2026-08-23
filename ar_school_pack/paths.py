"""
Centralized production-safe paths for AR School Management System.
"""

import os
import sys
from pathlib import Path


APP_NAME = "AR School Management"
VENDOR_NAME = "AR Software Solutions"


def get_user_data_dir() -> Path:
    """Return writable per-user application data directory."""

    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / VENDOR_NAME / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / VENDOR_NAME / APP_NAME

    # Linux / Chromebook
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "ar-school"

    return Path.home() / ".local" / "share" / "ar-school"


def get_reports_dir() -> Path:
    path = get_user_data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_backups_dir() -> Path:
    path = get_user_data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    path = get_user_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_database_path() -> Path:
    path = get_user_data_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path / "school_system.db"


def get_resource_dir() -> Path:
    """Return read-only application resource directory."""

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

    return Path(__file__).resolve().parent
