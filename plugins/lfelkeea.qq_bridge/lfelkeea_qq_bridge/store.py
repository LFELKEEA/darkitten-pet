from __future__ import annotations

"""本地数据：人格卡、记忆、黑话、聊天习惯和运行状态。

全部写入 data/plugins/.state/lfelkeea.qq_bridge/，不联网、不上传。
"""

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any


PLUGIN_ID = "lfelkeea.qq_bridge"
MAX_PERSONA = 8000


def _now() -> float:
    return time.time()


def _norm(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?；;：:]+", "", str(text or "").lower())


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _empty_habit() -> dict[str, Any]:
    return {
        "count": 0,
        "chars": 0,
        "short": 0,
        "question": 0,
        "exclaim": 0,
        "ellipsis": 0,
        "faces": 0,
        "hours": [0] * 24,
        "samples": [],
        "updated_at": 0.0,
    }


class JsonStore:
    def __init__(self, root: Path, default_persona_path: Path | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.persona_path = self.root / "persona.md"
        self.memory_path = self.root / "memory.json"
        self.slang_path = self.root / "slang.json"
        self.habits_path = self.root / "habits.json"
        self.runtime_path = self.root / "runtime.json"

        self.memory = self._read_json(self.memory_path, {"items": []})
        self.slang = self._read_json(self.slang_path, {"terms": {}})
        self.habits = self._read_json(self.habits_path, {"conversations": {}})
        self.runtime = self._read_json(
            self.runtime_path,
            {"inbox": [], "recent": {}, "seen": [], "last_compact_at": 0, "connection_ok": None},
        )

        if not isinstance(self.memory.get("items"), list):
            self.memory["items"] = []
        if not isinstance(self.slang.get("terms"), dict):
            self.slang["terms"] = {}
        if not isinstance(self.habits.get("conversations"), dict):
            self.habits["conversations"] = {}
        if not isinstance(self.runtime.get("inbox"), list):
            self.runtime["inbox"] = []
        if not isinstance(self.runtime.get("recent"), dict):
            self.runtime["recent"] = {}
        if not isinstance(self.runtime.get("seen"), list):
            self.runtime["seen"] = []

        self._dirty: set[str] = set()
        self._ensure_persona(default_persona_path)

    # ---------- 基础读写 ----------

    @staticmethod
    def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else dict(default)
        except (OSError, ValueError):
            return dict(default)

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def _write_json(cls, path: Path, value: dict[str, Any]) -> None:
        cls._write_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    def flush(self) -> None:
        if not self._dirty:
            return
        if "memory" in self._dirty:
            self.memory["updated_at"] = _now()
            self._write_json(self.memory_path, self.memory)
        if "slang" in self._dirty:
            self.slang["updated_at"] = _now()
            self._write_json(self.slang_path, self.slang)
        if "habits" in self._dirty:
            self.habits["updated_at"] = _now()
            self._write_json(self.habits_path, self.habits)
        if "runtime" in self._dirty:
            self._write_json(self.runtime_path, self.runtime)
        self._dirty.clear()

    # ---------- 人格卡 ----------

    def _ensure_persona(self, default_persona_path: Path | None) -> None:
        try:
            existing = self.persona_path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return
        fallback = "# 人格卡\n\n你是一只桌面小猫，说话简短、自然。"
        if default_persona_path is not None:
            try:
                fallback = Path(default_persona_path).read_text(encoding="utf-8").strip() or fallback
            except OSError:
                pass
        self._write_text(self.persona_path, fallback + "\n")

    def persona(self) -> str:
        try:
            value = self.persona_path.read_text(encoding="utf-8").strip()
        except OSError:
            value = ""
        return value[:MAX_PERSONA] or "# 人格卡\n\n你是一只桌面小猫，说话简短、自然。"

    def set_persona(self, text: str) -> int:
        value = str(text or "").strip()
        if not value:
            raise ValueError("人格卡不能为空。")
        if len(value) > MAX_PERSONA:
            raise ValueError(f"人格卡最多 {MAX_PERSONA} 字。")
        self._write_text(self.persona_path, value + "\n")
        return len(value)

    # ---------- 记忆 ----------

    def add_memory(self, text: str, importance: int = 30, source: str = "qq") -> dict[str, Any] | None:
        value = " ".join(str(text or "").split()).strip()
        if not value:
            return None
        value = value[:240]
        now = _now()
        normalized = _norm(value)
        for item in self.memory["items"]:
            old = _norm(str(item.get("text", "")))
            if old == normalized or (len(normalized) >= 12 and old.startswith(normalized[:12])):
                item["last_seen_at"] = now
                item["hits"] = int(item.get("hits", 1)) + 1
                item["importance"] = max(
                    int(item.get("importance", 0)), _clamp(importance, 1, 100, 30)
                )
                self._dirty.add("memory")
                return item
        item = {
            "id": "mem_" + uuid.uuid4().hex[:10],
            "text": value,
            "importance": _clamp(importance, 1, 100, 30),
            "source": str(source or "qq")[:24],
            "created_at": now,
            "last_seen_at": now,
            "hits": 1,
        }
        self.memory["items"].append(item)
        self.prune_memory()
        self._dirty.add("memory")
        return item

    def prune_memory(self, max_items: int = 80) -> None:
        limit = _clamp(max_items, 10, 500, 80)
        items = self.memory["items"]
        if len(items) <= limit:
            return
        items.sort(
            key=lambda item: (
                int(item.get("importance", 0)),
                float(item.get("last_seen_at", 0)),
            ),
            reverse=True,
        )
        del items[limit:]

    def memories(self, limit: int = 8) -> list[dict[str, Any]]:
        items = sorted(
            self.memory["items"],
            key=lambda item: (
                int(item.get("importance", 0)),
                float(item.get("last_seen_at", 0)),
            ),
            reverse=True,
        )
        return [dict(item) for item in items[: max(1, int(limit))]]

    def remove_memory(self, keyword: str) -> int:
        needle = str(keyword or "").strip().lower()
        if not needle:
            return 0
        keep: list[dict[str, Any]] = []
        removed = 0
        for item in self.memory["items"]:
            text = str(item.get("text", "")).lower()
            item_id = str(item.get("id", "")).lower()
            if needle in text or needle == item_id:
                removed += 1
            else:
                keep.append(item)
        self.memory["items"] = keep
        if removed:
            self._dirty.add("memory")
        return removed

    # ---------- 黑话 ----------

    def add_slang(
        self, term: str, meaning: str, example: str = "", source: str = "manual"
    ) -> dict[str, Any] | None:
        key = " ".join(str(term or "").split()).strip()[:16]
        value = " ".join(str(meaning or "").split()).strip()[:80]
        if not key or not value:
            return None
        now = _now()
        entry = self.slang["terms"].get(key)
        if not isinstance(entry, dict):
            entry = {
                "term": key,
                "meaning": value,
                "example": "",
                "count": 0,
                "source": str(source or "manual")[:24],
                "updated_at": now,
            }
        entry["term"] = key
        entry["meaning"] = value
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["updated_at"] = now
        if example:
            entry["example"] = str(example)[:120]
        self.slang["terms"][key] = entry
        self.prune_slang()
        self._dirty.add("slang")
        return entry

    def prune_slang(self, max_terms: int = 200) -> None:
        limit = _clamp(max_terms, 20, 1000, 200)
        terms = self.slang["terms"]
        if len(terms) <= limit:
            return
        ordered = sorted(
            terms.items(),
            key=lambda pair: (
                int(pair[1].get("count", 0)) if isinstance(pair[1], dict) else 0,
                float(pair[1].get("updated_at", 0)) if isinstance(pair[1], dict) else 0,
            ),
            reverse=True,
        )
        self.slang["terms"] = dict(ordered[:limit])

    def slang_terms(self) -> dict[str, dict[str, Any]]:
        return {
            key: dict(value)
            for key, value in self.slang["terms"].items()
            if isinstance(value, dict)
        }

    def remove_slang(self, term: str) -> bool:
        key = str(term or "").strip()
        if not key:
            return False
        if key in self.slang["terms"]:
            del self.slang["terms"][key]
            self._dirty.add("slang")
            return True
        lowered = key.lower()
        for existing in list(self.slang["terms"]):
            if existing.lower() == lowered:
                del self.slang["terms"][existing]
                self._dirty.add("slang")
                return True
        return False

    # ---------- 聊天习惯 ----------

    def observe(
        self, conversation: str, text: str, face_count: int = 0, hour: int | None = None
    ) -> None:
        conv = str(conversation or "").strip()
        if not conv:
            return
        value = str(text or "")
        data = self.habits["conversations"].get(conv)
        if not isinstance(data, dict):
            data = _empty_habit()
            self.habits["conversations"][conv] = data
        data["count"] = int(data.get("count", 0)) + 1
        data["chars"] = int(data.get("chars", 0)) + len(value)
        data["short"] = int(data.get("short", 0)) + (1 if len(value) <= 10 else 0)
        data["question"] = int(data.get("question", 0)) + (
            1 if ("?" in value or "？" in value) else 0
        )
        data["exclaim"] = int(data.get("exclaim", 0)) + (
            1 if ("!" in value or "！" in value) else 0
        )
        data["ellipsis"] = int(data.get("ellipsis", 0)) + (
            1 if ("..." in value or "…" in value) else 0
        )
        data["faces"] = int(data.get("faces", 0)) + max(0, int(face_count))
        hours = data.get("hours")
        if not isinstance(hours, list) or len(hours) != 24:
            hours = [0] * 24
            data["hours"] = hours
        current_hour = time.localtime().tm_hour if hour is None else int(hour)
        if 0 <= current_hour <= 23:
            hours[current_hour] = int(hours[current_hour]) + 1
        samples = data.get("samples")
        if not isinstance(samples, list):
            samples = []
            data["samples"] = samples
        samples.append(value[:80])
        if len(samples) > 20:
            del samples[: len(samples) - 20]
        data["updated_at"] = _now()
        self.prune_habits()
        self._dirty.add("habits")

    def prune_habits(self, max_conversations: int = 100) -> None:
        conversations = self.habits["conversations"]
        if len(conversations) <= max_conversations:
            return
        ordered = sorted(
            conversations.items(),
            key=lambda pair: float(pair[1].get("updated_at", 0))
            if isinstance(pair[1], dict)
            else 0,
            reverse=True,
        )
        self.habits["conversations"] = dict(ordered[:max_conversations])

    def habit(self, conversation: str) -> dict[str, Any]:
        data = self.habits["conversations"].get(str(conversation or ""))
        return dict(data) if isinstance(data, dict) else _empty_habit()

    def remove_habit(self, conversation: str) -> bool:
        key = str(conversation or "").strip()
        if not key:
            return False
        conversations = self.habits["conversations"]
        if key not in conversations:
            return False
        del conversations[key]
        self._dirty.add("habits")
        return True

    # ---------- 运行状态 ----------

    def append_recent(
        self, conversation: str, role: str, text: str, limit: int = 20, name: str = ""
    ) -> None:
        conv = str(conversation or "").strip()
        if not conv:
            return
        recent = self.runtime["recent"].get(conv)
        if not isinstance(recent, list):
            recent = []
            self.runtime["recent"][conv] = recent
        recent.append(
            {
                "role": str(role or "user"),
                "name": str(name or "")[:40],
                "text": str(text or "")[:400],
                "ts": _now(),
            }
        )
        if len(recent) > limit:
            del recent[: len(recent) - limit]
        self._dirty.add("runtime")

    def recent(self, conversation: str, limit: int = 8) -> list[dict[str, Any]]:
        recent = self.runtime["recent"].get(str(conversation or ""))
        if not isinstance(recent, list):
            return []
        return [dict(item) for item in recent[-max(1, int(limit)) :] if isinstance(item, dict)]

    def append_inbox(
        self,
        conversation: str,
        role: str,
        text: str,
        name: str = "",
        limit: int = 300,
    ) -> None:
        inbox = self.runtime["inbox"]
        inbox.append(
            {
                "conversation": str(conversation or ""),
                "role": str(role or "user"),
                "name": str(name or "")[:40],
                "text": str(text or "")[:600],
                "ts": _now(),
            }
        )
        if len(inbox) > limit:
            del inbox[: len(inbox) - limit]
        self._dirty.add("runtime")

    def inbox(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.runtime["inbox"] if isinstance(item, dict)]

    def clear_inbox(self, keep: int = 3) -> None:
        inbox = self.runtime["inbox"]
        keep = max(0, int(keep))
        if keep:
            del inbox[: max(0, len(inbox) - keep)]
        else:
            inbox.clear()
        self._dirty.add("runtime")

    def mark_seen(self, message_id: str, limit: int = 500) -> None:
        value = str(message_id or "").strip()
        if not value:
            return
        seen = self.runtime["seen"]
        if value in seen:
            return
        seen.append(value)
        if len(seen) > limit:
            del seen[: len(seen) - limit]
        self._dirty.add("runtime")

    def seen(self, message_id: str) -> bool:
        value = str(message_id or "").strip()
        return bool(value) and value in self.runtime["seen"]

    def set_connection(self, ok: bool | None) -> None:
        self.runtime["connection_ok"] = ok
        self._dirty.add("runtime")

    def mark_compacted(self, timestamp: float | None = None) -> None:
        self.runtime["last_compact_at"] = _now() if timestamp is None else float(timestamp)
        self._dirty.add("runtime")
