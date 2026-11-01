from __future__ import annotations

"""和 QQ 桥接插件之间的只读/单向通道。

- 导出：`export/context.json`（状态、成长段、规则）和 `export/memories.json`（可召回条目）；
- 收件箱：`inbox.jsonl`，QQ 桥接追加情节、召回回执、更正和“好奇”；
- 收件箱按字节偏移读取，处理完不删除数据，只在体积过大时压缩已读部分。

两边都不理解对方的内部数据结构，只依赖这里声明的 JSON 字段。
"""

import json
import os
from pathlib import Path
from typing import Any

from . import model

EXPORT_VERSION = 1
INBOX_MAX_BYTES = 2 * 1024 * 1024


class Link:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.export_dir = self.root / "export"
        self.context_path = self.export_dir / "context.json"
        self.memories_path = self.export_dir / "memories.json"
        self.inbox_path = self.root / "inbox.jsonl"
        self.cursor_path = self.root / "inbox-cursor.json"
        self.cursor: dict[str, Any] = model.read_json(self.cursor_path, {}) or {}

    # ---------- 导出 ----------

    def export_context(self, payload: dict[str, Any]) -> None:
        data = dict(payload)
        data["version"] = EXPORT_VERSION
        data["generated_at"] = model.now()
        model.atomic_write_json(self.context_path, data)

    def export_memories(self, items: list[dict[str, Any]], reference: float | None = None) -> None:
        base = reference if reference is not None else model.now()
        model.atomic_write_json(
            self.memories_path,
            {
                "version": EXPORT_VERSION,
                "generated_at": base,
                "items": items,
            },
        )

    def context(self) -> dict[str, Any]:
        raw = model.read_json(self.context_path, {})
        return raw if isinstance(raw, dict) else {}

    def reading(self) -> dict[str, Any]:
        try:
            stat = self.context_path.stat()
        except OSError:
            return {"exists": False, "age_seconds": 99999, "generated_at": 0}
        return {
            "exists": True,
            "age_seconds": round(model.now() - stat.st_mtime, 1),
            "generated_at": model.as_float(self.context().get("generated_at")),
        }

    # ---------- 收件箱 ----------

    def read_inbox(self, limit: int = 80) -> list[dict[str, Any]]:
        try:
            size = self.inbox_path.stat().st_size
        except OSError:
            return []
        offset = model.as_int(self.cursor.get("offset"))
        if offset < 0 or offset > size:
            offset = 0
        try:
            with open(self.inbox_path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read(1024 * 512)
                new_offset = offset + len(chunk)
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        consumed = 0
        for raw_line in chunk.splitlines(keepends=True):
            if not raw_line.endswith(b"\n"):
                # 最后一行可能正在写入，下次再读。
                break
            consumed += len(raw_line)
            text = raw_line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                data = json.loads(text)
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
                if len(rows) >= max(1, int(limit)):
                    break
        if consumed:
            self.cursor = {"offset": offset + consumed, "updated_at": model.now()}
            try:
                model.atomic_write_json(self.cursor_path, self.cursor)
            except OSError:
                pass
        self.compact_inbox()
        return rows

    def compact_inbox(self) -> None:
        try:
            size = self.inbox_path.stat().st_size
        except OSError:
            return
        offset = model.as_int(self.cursor.get("offset"))
        if size <= INBOX_MAX_BYTES or offset <= size // 2:
            return
        try:
            with open(self.inbox_path, "rb") as handle:
                handle.seek(offset)
                rest = handle.read()
            tmp = self.inbox_path.with_suffix(".tmp")
            tmp.write_bytes(rest)
            os.replace(tmp, self.inbox_path)
            self.cursor = {"offset": 0, "updated_at": model.now()}
            model.atomic_write_json(self.cursor_path, self.cursor)
        except OSError:
            pass

    def cursor_snapshot(self) -> dict[str, Any]:
        try:
            size = self.inbox_path.stat().st_size
        except OSError:
            size = 0
        return {
            "offset": model.as_int(self.cursor.get("offset")),
            "size": size,
            "updated_at": model.as_float(self.cursor.get("updated_at")),
        }
