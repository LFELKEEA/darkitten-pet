"""只读地收集灵感来源：QQ 桥接记忆、桌宠对话、手动导入的聊天记录。

原则：
- 只读，不修改其他插件的文件；
- 跳过配置、密钥类文件；
- 所有条目都带上来源标签，方便在灵感库里区分。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

QQ_SOURCE = "QQ 桥接记忆"
PET_SOURCE = "桌宠对话"
IMPORT_SOURCE = "导入对话"

# 记忆条目里常见的正文字段名
_TEXT_KEYS = ("text", "content", "memory", "summary", "value", "note", "body")
# 记忆条目里常见的附加字段
_META_KEYS = (
    "time",
    "ts",
    "timestamp",
    "created_at",
    "date",
    "source",
    "who",
    "user",
    "group",
    "tags",
    "kind",
    "type",
)

# 绝不读取的文件（可能含密钥）
_SKIP_NAMES = {
    "config.json",
    "settings.json",
    "credentials.json",
    "secrets.json",
    "social.json",
}
_MAX_FILE_BYTES = 16 * 1024 * 1024


def _read_json(path: str) -> Any:
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _format_time(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, (int, float)):
        stamp = float(value)
        if stamp > 10_000_000_000:
            stamp /= 1000.0
        try:
            return time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
        except (OverflowError, OSError, ValueError):
            return ""
    return str(value).strip()[:32]


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _looks_like_memory(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    return any(isinstance(item.get(key), str) and item.get(key).strip() for key in _TEXT_KEYS)


def _text_of(item: dict) -> str:
    for key in _TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _meta_of(item: dict) -> dict:
    meta: dict[str, Any] = {}
    for key in _META_KEYS:
        if key in item and item[key] not in (None, "", [], {}):
            meta[key] = item[key]
    if "created_at" in meta:
        meta["created_at"] = _format_time(meta["created_at"])
    elif "time" in meta:
        meta["created_at"] = _format_time(meta["time"])
    return meta


def _walk(node: Any, path: str, out: list[dict]) -> None:
    """递归找 list[dict] / list[str] 形式的记忆数组。"""
    if isinstance(node, list):
        hits = [item for item in node if _looks_like_memory(item)]
        if hits and len(hits) >= max(1, len(node) // 2):
            for item in hits:
                out.append({"text": _text_of(item), "meta": _meta_of(item), "from": path})
            return
        strings = [item for item in node if isinstance(item, str) and len(item.strip()) >= 4]
        if strings and len(strings) >= max(1, len(node) // 2):
            for item in strings:
                out.append({"text": item.strip(), "meta": {}, "from": path})
            return
        for index, item in enumerate(node):
            _walk(item, f"{path}[{index}]", out)
    elif isinstance(node, dict):
        values = list(node.values())
        hits = [value for value in values if _looks_like_memory(value)]
        if hits and len(hits) >= max(1, len(values) // 2):
            for value in hits:
                out.append({"text": _text_of(value), "meta": _meta_of(value), "from": path})
            return
        for key, value in node.items():
            _walk(value, f"{path}.{key}" if path else key, out)


def _json_files(root: str) -> list[str]:
    found: list[str] = []
    if not os.path.isdir(root):
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in ("__pycache__", ".git", "node_modules", "logs", "sessions", "tts")
        ]
        for name in filenames:
            low = name.lower()
            if not low.endswith(".json") or low in _SKIP_NAMES:
                continue
            found.append(os.path.join(dirpath, name))
    return found


def _qq_files(data_dir: str) -> list[str]:
    base = os.path.join(data_dir, "plugins")
    candidates = [
        os.path.join(base, ".state", "lfelkeea.qq_bridge", "memory.json"),
        os.path.join(base, "lfelkeea.qq_bridge", "memory.json"),
        os.path.join(data_dir, "qq_bridge", "memory.json"),
    ]
    files = [path for path in candidates if os.path.isfile(path)]
    if files:
        return files
    # 兼容旧安装：只在 QQ 桥接目录里找 memory 类文件，不扫整个数据目录。
    for root in (
        os.path.join(base, ".state", "lfelkeea.qq_bridge"),
        os.path.join(base, "lfelkeea.qq_bridge"),
    ):
        for path in _json_files(root):
            name = os.path.basename(path).lower()
            if "memory" in name or "episode" in name:
                files.append(path)
    return list(dict.fromkeys(files))


def _alive_files(data_dir: str) -> list[str]:
    base = os.path.join(data_dir, "plugins")
    candidates = [
        os.path.join(base, ".state", "lfelkeea.alive_core", "memory.json"),
        os.path.join(base, "lfelkeea.alive_core", "memory.json"),
    ]
    return [path for path in candidates if os.path.isfile(path)]


def _relative(path: str, data_dir: str) -> str:
    try:
        return os.path.relpath(path, data_dir)
    except ValueError:
        return os.path.basename(path)


def load_qq_memories(data_dir: str, limit: int = 500) -> tuple[list[dict], list[str]]:
    """返回 (QQ 桥接记忆, 实际扫描过的文件)。"""
    memories: list[dict] = []
    scanned: list[str] = []
    seen: set[str] = set()
    for path in _qq_files(data_dir):
        scanned.append(path)
        data = _read_json(path)
        if data is None:
            continue
        found: list[dict] = []
        _walk(data, "", found)
        for item in found:
            text = _clean_text(item.get("text"))
            key = " ".join(text.split()).casefold()
            if len(text) < 4 or key in seen:
                continue
            seen.add(key)
            meta = dict(item.get("meta") or {})
            meta.setdefault("created_at", _format_time(meta.get("created_at") or meta.get("time")))
            memories.append(
                {
                    "text": text,
                    "meta": meta,
                    "source": QQ_SOURCE,
                    "from": _relative(path, data_dir),
                }
            )
    return memories[-limit:], scanned


def _alive_episodes(data_dir: str, limit: int) -> tuple[list[dict], list[str]]:
    memories: list[dict] = []
    scanned: list[str] = []
    seen: set[str] = set()
    for path in _alive_files(data_dir):
        scanned.append(path)
        data = _read_json(path)
        if not isinstance(data, dict):
            continue
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            continue
        for item in episodes:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            source = str(item.get("source") or "").strip()
            if source == "qq":
                continue
            if kind and kind not in ("chat", "screen", "proactive", "pet"):
                continue
            text = _clean_text(item.get("text"))
            key = " ".join(text.split()).casefold()
            if len(text) < 4 or key in seen:
                continue
            seen.add(key)
            memories.append(
                {
                    "text": text,
                    "meta": {
                        "created_at": _format_time(item.get("at")),
                        "kind": kind,
                        "source": source,
                    },
                    "source": PET_SOURCE,
                    "from": _relative(path, data_dir),
                    "_at": item.get("at") or 0,
                }
            )
    memories.sort(key=lambda item: _as_float(item.get("_at")))
    for item in memories:
        item.pop("_at", None)
    return memories[-limit:], scanned


def load_pet_memories(data_dir: str, limit: int = 500) -> tuple[list[dict], list[str]]:
    """读取活人感内核保存的桌宠对话/观察记录，排除 QQ 来源。"""
    return _alive_episodes(data_dir, limit)


def live_conversation_items(api: Any, limit: int = 24, chunk: int = 6) -> list[dict]:
    """读取当前桌宠会话的快照。只读，拿不到就返回空列表。"""
    try:
        conversation = getattr(getattr(api, "manager", None), "conversation", None)
        getter = getattr(conversation, "messages", None)
        if not callable(getter):
            return []
        try:
            rows = getter(limit)
        except TypeError:
            rows = getter()
    except Exception:
        return []
    if not isinstance(rows, list):
        return []

    messages: list[tuple[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip()
        content = _clean_text(row.get("content"))
        if role not in ("user", "assistant") or not content:
            continue
        messages.append(("主人" if role == "user" else "桌宠", content))
    if not messages:
        return []

    items: list[dict] = []
    for index in range(0, len(messages), max(1, chunk)):
        block = messages[index : index + max(1, chunk)]
        text = "\n".join(f"{speaker}：{content}" for speaker, content in block)
        items.append(
            {
                "text": text,
                "meta": {"created_at": "", "kind": "chat", "source": "live"},
                "source": PET_SOURCE,
                "from": "当前会话",
            }
        )
    return items


def captured_items(api: Any) -> list[dict]:
    """读取运行中抓到的桌宠发言（chat.reply / screen.comment）。"""
    try:
        raw = api.storage.get("pet_chat_log")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            text = _clean_text(entry)
            created = ""
            kind = ""
        elif isinstance(entry, dict):
            text = _clean_text(entry.get("text"))
            created = str(entry.get("created") or "")
            kind = str(entry.get("kind") or "")
        else:
            continue
        if len(text) < 4:
            continue
        items.append(
            {
                "text": text,
                "meta": {"created_at": created, "kind": kind, "source": "capture"},
                "source": PET_SOURCE,
                "from": "实时抓取",
            }
        )
    return items


def record_capture(api: Any, text: str, kind: str = "chat", keep: int = 120) -> bool:
    """把桌宠刚说的话记到插件自己的 storage，最多保留 keep 条。"""
    body = _clean_text(text)
    if len(body) < 4:
        return False
    try:
        raw = api.storage.get("pet_chat_log")
        log = list(raw) if isinstance(raw, list) else []
        last = log[-1] if log and isinstance(log[-1], dict) else {}
        if last.get("text") == body and last.get("kind") == kind:
            return False
        log.append(
            {
                "text": body[:600],
                "kind": str(kind or "chat")[:24],
                "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        api.storage["pet_chat_log"] = log[-max(1, int(keep)) :]
        api.save_storage()
        return True
    except Exception:
        return False


def imported_items(api: Any) -> list[dict]:
    try:
        raw = api.storage.get("imported_chats")
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    items: list[dict] = []
    for entry in raw:
        if isinstance(entry, str):
            text = _clean_text(entry)
            created = ""
        elif isinstance(entry, dict):
            text = _clean_text(entry.get("text"))
            created = str(entry.get("created") or "")
        else:
            continue
        if len(text) < 4:
            continue
        items.append(
            {
                "text": text,
                "meta": {"created_at": created, "kind": "import", "source": "import"},
                "source": IMPORT_SOURCE,
                "from": "手动导入",
            }
        )
    return items


def _chunk_import(text: str, max_chars: int = 500, max_lines: int = 6) -> list[str]:
    lines = [line.strip() for line in _clean_text(text).split("\n") if line.strip()]
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if len(line) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current = []
                size = 0
            for start in range(0, len(line), max_chars):
                chunks.append(line[start : start + max_chars])
            continue
        if current and (size + len(line) + 1 > max_chars or len(current) >= max_lines):
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def store_imported_text(api: Any, text: str, keep: int = 30) -> int:
    """把粘贴的聊天记录切成小段，存进插件 storage。"""
    chunks = _chunk_import(text)
    if not chunks:
        return 0
    try:
        raw = api.storage.get("imported_chats")
        entries = list(raw) if isinstance(raw, list) else []
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        for chunk in chunks:
            entries.append({"text": chunk[:1000], "created": stamp})
        api.storage["imported_chats"] = entries[-max(1, int(keep)) :]
        api.save_storage()
        return len(chunks)
    except Exception:
        return 0


def gather_pet_items(
    api: Any,
    data_dir: str,
    limit: int = 60,
) -> tuple[list[dict], dict[str, int]]:
    """合并所有桌宠对话来源，按新鲜度优先并去重。"""
    live = live_conversation_items(api)
    captured = captured_items(api)
    imported = imported_items(api)
    alive, scanned = load_pet_memories(data_dir, limit=limit)

    details = {
        "live": len(live),
        "captured": len(captured),
        "imported": len(imported),
        "alive": len(alive),
        "alive_files": len(scanned),
    }

    combined: list[dict] = []
    seen: set[str] = set()
    # 当前会话最新鲜，其次是运行中抓到的发言，再到手动导入和内核历史。
    for group in (live, captured, imported, reversed(alive)):
        for item in group:
            text = _clean_text(item.get("text"))
            key = " ".join(text.split()).casefold()
            if len(text) < 4 or key in seen:
                continue
            seen.add(key)
            combined.append(item)
            if len(combined) >= limit:
                return combined, details
    return combined, details


def clear_captured(api: Any) -> int:
    try:
        raw = api.storage.get("pet_chat_log")
        count = len(raw) if isinstance(raw, list) else 0
        api.storage["pet_chat_log"] = []
        api.save_storage()
        return count
    except Exception:
        return 0


def clear_imports(api: Any) -> int:
    try:
        raw = api.storage.get("imported_chats")
        count = len(raw) if isinstance(raw, list) else 0
        api.storage["imported_chats"] = []
        api.save_storage()
        return count
    except Exception:
        return 0
