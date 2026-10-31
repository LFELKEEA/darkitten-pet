from __future__ import annotations

import sys
from pathlib import Path


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ASSET_DIR = resource_dir() / "assets"
DATA_DIR = app_dir() / "data"
CONFIG_PATH = DATA_DIR / "config.json"
