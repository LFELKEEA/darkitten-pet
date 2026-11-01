from __future__ import annotations

"""把每次自开发任务写成 JSONL 会话日志，方便回看和排错。

日志只保存在本机 data/dev/sessions/，不包含 AI 密钥。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def _safe(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, dict):
        return {
            str(key): _safe(item, depth + 1)
            for key, item in value.items()
            if str(key).lower() not in ("api_key", "apikey", "authorization", "token", "password")
        }
    if isinstance(value, (list, tuple)):
        return [_safe(item, depth + 1) for item in value[:200]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


class SessionLog:
    def __init__(self, sessions_dir: Path, task: str) -> None:
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", task.strip())[:24].strip("-") or "task"
        self.path = self.dir / f"{stamp}-{slug}.jsonl"
        self._handle = None
        try:
            self._handle = self.path.open("a", encoding="utf-8")
        except OSError:
            self._handle = None
        self.write({"type": "task", "task": task})

    def write(self, event: dict[str, Any]) -> None:
        if self._handle is None:
            return
        payload = {"ts": datetime.now().isoformat(timespec="seconds"), **_safe(event)}
        try:
            self._handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._handle.flush()
        except (OSError, TypeError, ValueError):
            pass

    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
