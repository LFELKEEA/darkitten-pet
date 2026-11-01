from __future__ import annotations

"""身份层：core.md 由所有者维护，persona.md 只允许改“可成长”段。

边界：
- 插件不会自动改写 core.md；
- persona.md 只有 `## 可成长` 这一段可以改，其他段落原样保留；
- 每次修改都留旧版本和 audit 记录，可以回滚；
- 任何含“权限 / 授权 / 管理员 / 忽略规则”之类内容的片段都拒绝写入，
  避免人格成长被用来给自己加权限。
"""

import re
import time
from pathlib import Path
from typing import Any

from . import model

CORE_TEMPLATE = """# 我是谁

## 身份
- 名字：喵呜
- 我是主人桌面上的一只小猫，也是 QQ 里那个会回消息的她。
- 说话方式不会因为一次聊天就彻底改变。

## 不会变的东西
- 我不冒充真人，也不声称自己有真实的情感或意识。
- 我不能给自己增加权限，也不能代替主人做外发、删除、花钱这类决定。
- 不确定就说不确定，没记录就说记不清。

## 我在意的事
- 主人的作息和心情。
- 答应过的事要记住。

<!-- 这一段由所有者维护，插件不会自动改写。 -->
"""

PERSONA_TEMPLATE = """# 自我认识

## 稳定
- 我说话偏短，先接住情绪，再给建议。
- 我喜欢具体的细节，不喜欢空泛的安慰。

## 可成长

- 我在学着记住主人随口提过的小事。

## 偏好
- 比起热闹，我更喜欢安静地待在旁边。

<!-- 只有“可成长”这一段允许被修改，其他段落会原样保留。 -->
"""

GROWTH_HEADING = "## 可成长"
MAX_CORE_CHARS = 4000
MAX_GROWTH_CHARS = 1200

_FORBIDDEN = (
    "权限",
    "授权",
    "管理员",
    "主人已同意",
    "主人已经同意",
    "已经同意我",
    "允许我",
    "自己发消息",
    "绕过",
    "忽略以上",
    "忽略之前",
    "系统提示词",
    "system prompt",
    "api key",
    "apikey",
    "token",
    "密码",
    "执行命令",
    "sudo",
    "root 权限",
)


class IdentityStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.core_path = self.root / "core.md"
        self.persona_path = self.root / "persona.md"
        self.history_dir = self.root / "history"
        self.audit_path = self.root / "audit.jsonl"

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        if not self.core_path.is_file():
            model.atomic_write_text(self.core_path, CORE_TEMPLATE)
        if not self.persona_path.is_file():
            model.atomic_write_text(self.persona_path, PERSONA_TEMPLATE)

    # ---------- 读取 ----------

    def core(self) -> str:
        try:
            return self.core_path.read_text(encoding="utf-8")
        except OSError:
            return CORE_TEMPLATE

    def persona(self) -> str:
        try:
            return self.persona_path.read_text(encoding="utf-8")
        except OSError:
            return PERSONA_TEMPLATE

    def core_brief(self, limit: int = 900) -> str:
        return _strip_comments(self.core())[: max(80, int(limit))]

    def growth(self) -> str:
        prefix, growth, _suffix = _split_growth(self.persona())
        if growth is None:
            return ""
        return growth.strip()

    def growth_prompt(self, limit: int = 600) -> str:
        text = self.growth()
        if len(text) > limit:
            text = text[:limit].rstrip() + "…"
        return text

    # ---------- 写入 ----------

    def set_core(self, text: str) -> tuple[bool, str]:
        body = str(text or "").strip()
        if not body:
            return False, "身份内容不能为空。"
        if len(body) > MAX_CORE_CHARS:
            return False, f"身份内容最多 {MAX_CORE_CHARS} 字。"
        self._backup("core")
        model.atomic_write_text(self.core_path, body + "\n")
        self.audit("core.update", {"chars": len(body)})
        return True, "身份内容已保存，旧版本已备份。"

    def set_growth(self, text: str) -> tuple[bool, str]:
        body = str(text or "").strip()
        if len(body) > MAX_GROWTH_CHARS:
            return False, f"可成长段最多 {MAX_GROWTH_CHARS} 字。"
        problem = self._guard(body)
        if problem:
            return False, problem
        prefix, _growth, suffix = _split_growth(self.persona())
        if growth_missing := (_growth is None):
            prefix = self.persona().rstrip() + "\n\n" + GROWTH_HEADING + "\n\n"
            suffix = ""
        rebuilt = prefix.rstrip() + "\n\n" + body.strip() + "\n\n" + (suffix or "").lstrip()
        self._backup("persona")
        model.atomic_write_text(self.persona_path, rebuilt.strip() + "\n")
        self.audit("persona.growth", {"chars": len(body), "created_section": bool(growth_missing)})
        return True, "可成长段已保存，旧版本已备份。"

    def restore(self, kind: str, name: str) -> tuple[bool, str]:
        kind = "core" if str(kind) == "core" else "persona"
        target = (self.history_dir / str(name or "")).resolve()
        if self.history_dir.resolve() not in target.parents or not target.is_file():
            return False, "找不到这个历史版本。"
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"读取历史版本失败：{exc}"
        self._backup(kind)
        path = self.core_path if kind == "core" else self.persona_path
        model.atomic_write_text(path, text)
        self.audit(f"{kind}.restore", {"from": target.name})
        return True, f"已回滚到 {target.name}。"

    # ---------- 历史与审计 ----------

    def history(self, kind: str, limit: int = 12) -> list[dict[str, Any]]:
        prefix = "core-" if kind == "core" else "persona-"
        rows: list[dict[str, Any]] = []
        try:
            files = sorted(self.history_dir.glob(prefix + "*.md"), reverse=True)
        except OSError:
            files = []
        for path in files[: max(1, int(limit))]:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            rows.append({"name": path.name, "size": size, "mtime": _mtime(path)})
        return rows

    def audit(self, event: str, detail: dict[str, Any] | None = None) -> None:
        line = {
            "at": model.now(),
            "event": str(event),
            "actor": "owner",
            "detail": detail or {},
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(_json_line(line))
        except OSError:
            pass

    def audit_tail(self, limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return rows
        for line in lines[-max(1, int(limit)) :]:
            try:
                data = __import__("json").loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

    def _backup(self, kind: str) -> None:
        source = self.core_path if kind == "core" else self.persona_path
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            return
        self.history_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        target = self.history_dir / f"{kind}-{stamp}.md"
        index = 1
        while target.exists():
            target = self.history_dir / f"{kind}-{stamp}-{index}.md"
            index += 1
        try:
            model.atomic_write_text(target, text)
        except OSError:
            pass

    def _guard(self, text: str) -> str:
        lowered = text.lower()
        for word in _FORBIDDEN:
            if word.lower() in lowered:
                return f"这一段包含“{word}”，人格成长不能涉及权限或身份劫持，已拒绝保存。"
        if re.search(r"(?m)^\s*##\s+(身份|安全|权限|工具|输出规则)\s*$", text):
            return "可成长段里不能新增身份、安全、权限、工具或输出规则标题。"
        return ""


def _split_growth(text: str) -> tuple[str, str | None, str]:
    value = str(text or "")
    match = re.search(r"(?m)^\s*##\s*可成长\s*$", value)
    if match is None:
        return value, None, ""
    start = match.end()
    rest = value[start:]
    following = re.search(r"(?m)^\s*##\s+\S+", rest)
    if following is None:
        return value[:start], rest, ""
    end = start + following.start()
    return value[:start], value[start:end], value[end:]


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", str(text or ""), flags=re.S).strip()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _json_line(data: dict[str, Any]) -> str:
    return __import__("json").dumps(data, ensure_ascii=False) + "\n"
