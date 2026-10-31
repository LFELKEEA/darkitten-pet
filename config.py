from __future__ import annotations

import copy
import json
import os
from typing import Any

from app_paths import CONFIG_PATH, DATA_DIR


DEFAULT_CONFIG: dict[str, Any] = {
    "first_run": True,
    "position": None,
    "always_on_top": True,
    "scale": 1.0,
    "idle_chatter": True,
    "idle_chatter_frequency": "normal",
    "voice_hint_seen": False,
    "ai": {
        "provider": "pollinations",
        "model": "openai-fast",
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "temperature": 0.85,
        "timeout": 30,
    },
    "screen_watch": {
        "enabled": False,
        "interval_seconds": 120,
        "mode": "auto",
        "min_idle_seconds": 45,
        "pause_when_fullscreen": True,
        "pause_sensitive": True,
        "max_edge": 1280,
        "jpeg_quality": 68,
        "vision_fallback": True,
        "last_comment": "",
    },
    "plugins": {
        "enabled": {},
        "settings": {},
    },
    "updates": {
        "auto_check": True,
        "ignored_version": "",
        "last_check": 0,
    },
}


SCREEN_WATCH_INTERVALS = (60, 120, 300, 600)
SCREEN_WATCH_MODES = ("auto", "window", "vision", "ocr")


IDLE_CHATTER_INTERVALS: dict[str, tuple[int, int]] = {
    "rare": (300_000, 600_000),
    "normal": (90_000, 200_000),
    "often": (45_000, 90_000),
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    def __init__(self, path=CONFIG_PATH) -> None:
        self.path = path
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self.data = _deep_merge(DEFAULT_CONFIG, loaded)
        except (OSError, ValueError):
            self.data = copy.deepcopy(DEFAULT_CONFIG)

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any, save: bool = False) -> None:
        self.data[key] = value
        if save:
            self.save()

    @property
    def ai(self) -> dict[str, Any]:
        return self.data.setdefault("ai", copy.deepcopy(DEFAULT_CONFIG["ai"]))
