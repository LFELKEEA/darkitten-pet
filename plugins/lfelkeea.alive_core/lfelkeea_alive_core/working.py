from __future__ import annotations

"""工作记忆：这一轮在聊什么、刚才说到哪、还有什么没弄明白。

它只在很短的窗口里有效，过期就淡出；写进这里的都是主人和她自己说过的话，
群友的发言不会进这里，避免把另一个会话的上下文串进来。
"""

from pathlib import Path
from typing import Any

from . import model

MAX_TURNS = 48
KEEP_SECONDS = 2 * 86_400


class WorkingMemory:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "working.json"
        self.data: dict[str, Any] = {
            "version": 1,
            "turns": [],
            "topic": "",
            "open_questions": [],
            "constraints": [],
            "updated_at": 0.0,
        }
        self.load()

    def load(self) -> None:
        raw = model.read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        turns = raw.get("turns")
        questions = raw.get("open_questions")
        constraints = raw.get("constraints")
        self.data = {
            "version": 1,
            "turns": [row for row in turns if isinstance(row, dict)] if isinstance(turns, list) else [],
            "topic": model.clean_text(raw.get("topic"), 80),
            "open_questions": [model.clean_text(row, 120) for row in questions if model.clean_text(row, 120)][:8]
            if isinstance(questions, list)
            else [],
            "constraints": [model.clean_text(row, 120) for row in constraints if model.clean_text(row, 120)][:8]
            if isinstance(constraints, list)
            else [],
            "updated_at": model.as_float(raw.get("updated_at")),
        }

    def save(self) -> None:
        model.atomic_write_json(self.path, self.data)

    # ---------- 写入 ----------

    def record(
        self,
        role: str,
        text: str,
        *,
        source: str = "pet",
        visibility: str = "private",
        at: float | None = None,
    ) -> None:
        body = model.clean_text(text, 300)
        if not body:
            return
        stamp = model.as_float(at) or model.now()
        turns = self.data.setdefault("turns", [])
        if turns:
            last = turns[-1]
            if (
                str(last.get("role")) == role
                and str(last.get("text")) == body
                and abs(model.as_float(last.get("at")) - stamp) < 2
            ):
                return
        turns.append(
            {
                "role": "assistant" if role in ("self", "assistant", "pet") else "user",
                "text": body,
                "source": str(source or "pet")[:16],
                "visibility": str(visibility or "private")[:48],
                "at": stamp,
            }
        )
        del turns[:-MAX_TURNS]
        cutoff = stamp - KEEP_SECONDS
        self.data["turns"] = [row for row in turns if model.as_float(row.get("at")) >= cutoff]
        if role not in ("self", "assistant", "pet"):
            self.data["topic"] = _topic(body)
        self.data["updated_at"] = stamp
        self.save()

    def add_question(self, text: str) -> None:
        body = model.clean_text(text, 120)
        if not body:
            return
        questions = self.data.setdefault("open_questions", [])
        if body not in questions:
            questions.append(body)
            del questions[:-8]
            self.data["updated_at"] = model.now()
            self.save()

    def resolve_question(self, keyword: str) -> int:
        key = model.clean_text(keyword, 40)
        if not key:
            return 0
        before = len(self.data.get("open_questions") or [])
        self.data["open_questions"] = [
            row for row in self.data.get("open_questions") or [] if key not in str(row)
        ]
        removed = before - len(self.data["open_questions"])
        if removed:
            self.save()
        return removed

    def clear(self) -> None:
        self.data["turns"] = []
        self.data["topic"] = ""
        self.data["open_questions"] = []
        self.data["constraints"] = []
        self.data["updated_at"] = model.now()
        self.save()

    # ---------- 读取 ----------

    def recent(
        self,
        *,
        limit: int = 6,
        viewer: str = "owner",
        channel: str = "private",
    ) -> list[dict[str, Any]]:
        allow_private = channel == "private"
        rows = [
            row
            for row in self.data.get("turns") or []
            if _visible(row.get("visibility"), viewer, channel, allow_private)
        ]
        return rows[-max(1, int(limit)):]

    def snapshot(self, *, viewer: str = "owner", channel: str = "private") -> dict[str, Any]:
        turns = self.recent(limit=8, viewer=viewer, channel=channel)
        return {
            "topic": self.data.get("topic", ""),
            "turns": [
                {
                    "role": row.get("role"),
                    "text": row.get("text"),
                    "source": row.get("source"),
                    "at": row.get("at"),
                }
                for row in turns
            ],
            "open_questions": list(self.data.get("open_questions") or []),
            "constraints": list(self.data.get("constraints") or []),
            "updated_at": self.data.get("updated_at", 0.0),
        }

    def prompt_block(
        self,
        *,
        viewer: str = "owner",
        channel: str = "private",
        limit: int = 6,
        budget: int = 420,
    ) -> str:
        turns = self.recent(limit=limit, viewer=viewer, channel=channel)
        lines: list[str] = []
        # topic 是从私聊/群聊原话里提的，不区分可见域，跨会话时宁可不提。
        if self.data.get("topic") and channel == "private":
            lines.append(f"这轮大概在聊：{model.clean_text(self.data.get('topic'), 60)}")
        if turns:
            lines.append("刚才的对话：")
            for row in turns:
                who = "你" if row.get("role") == "assistant" else "对方"
                if row.get("source") == "qq" and who == "对方":
                    who = "对方在 QQ 里"
                lines.append(f"- {who}：{model.clean_text(row.get('text'), 90)}")
            lines.append("接着这个上下文说，不要假装没听见上一句。")
        questions = [row for row in self.data.get("open_questions") or [] if row][:3]
        if questions:
            lines.append("")
            lines.append("还没弄明白的事：")
            for row in questions:
                lines.append(f"- {model.clean_text(row, 80)}")
            lines.append("可以承认不知道；不要说已经查过或编一个答案。")
        text = "\n".join(lines)
        if len(text) > max(120, int(budget)):
            text = text[: max(120, int(budget))].rstrip() + "…"
        return text


def _topic(text: str) -> str:
    value = model.clean_text(text, 60)
    for prefix in ("主人", "我"):
        if value.startswith(prefix) and len(value) > len(prefix) + 2:
            value = value[len(prefix):].lstrip("，。,.!！?？ ")
            break
    return value[:60]


def _visible(visibility: Any, viewer: str, channel: str, allow_private: bool) -> bool:
    value = str(visibility or "private")
    if value == "public":
        return True
    if channel != "private" and value == channel:
        return True
    if value.startswith("private:"):
        return allow_private and (value == viewer or viewer == "owner")
    if value == "private":
        return allow_private and viewer in ("owner", "private")
    if value == "owner":
        return allow_private and viewer == "owner"
    return value == viewer
