from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal


class Conversation(QObject):
    """聊天框、语音和屏幕观察共用的一条对话上下文。

    只保存 OpenAI 兼容接口需要的 role/content 结构，不保存截图或识别中间结果。
    """

    changed = Signal()

    def __init__(self, limit: int = 24, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.limit = max(4, int(limit))
        self._messages: list[dict[str, str]] = []

    def append(self, role: str, content: str) -> None:
        text = (content or "").strip()
        if not text:
            return
        self._messages.append({"role": role, "content": text})
        if len(self._messages) > self.limit:
            del self._messages[: len(self._messages) - self.limit]
        self.changed.emit()

    def messages(self, limit: int | None = None) -> list[dict[str, Any]]:
        items = self._messages if limit is None else self._messages[-max(1, limit) :]
        return [dict(item) for item in items]

    def last_user(self) -> str:
        for item in reversed(self._messages):
            if item.get("role") == "user":
                return item.get("content", "")
        return ""

    def clear(self) -> None:
        if not self._messages:
            return
        self._messages.clear()
        self.changed.emit()

    def __len__(self) -> int:
        return len(self._messages)
