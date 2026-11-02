from __future__ import annotations

"""技能、连接器、知识库共用的小工具。

这里只放真正会重复的底层操作：路径校验、原子写入、简单 frontmatter、
可恢复删除和敏感串打码。业务规则留在各自的 Store 里。
"""

import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
RESOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16", "latin-1")


class ResourceError(Exception):
    pass


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def read_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


def atomic_write(path: Path, content: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp-resource")
    tmp.write_bytes(content.encode("utf-8"))
    os.replace(tmp, target)


def safe_relative(raw: Any, *, allow_empty: bool = False) -> Path:
    text = str(raw or "").strip().replace("\\", "/")
    if text.startswith("./"):
        text = text[2:]
    if not text:
        if allow_empty:
            return Path(".")
        raise ResourceError("路径不能为空。")
    if text.startswith("/") or re.match(r"^[a-zA-Z]:", text):
        raise ResourceError("只接受相对路径。")
    parts = Path(text).parts
    if any(part == ".." for part in parts):
        raise ResourceError("路径里不能出现 ..。")
    return Path(*parts)


def inside(root: Path, target: Path) -> bool:
    root = Path(root).resolve()
    target = Path(target).resolve()
    return target == root or root in target.parents


def trash_path(path: Path, trash_root: Path, kind: str) -> tuple[bool, str]:
    """优先移到系统回收站；宿主模块不可用时退回到开发区自己的 .trash。"""
    source = Path(path)
    if not source.exists():
        return False, "目标已经不存在。"
    try:  # 桌宠运行时通常可用
        from system_utils import move_to_trash  # noqa: PLC0415

        ok, message = move_to_trash(source)
        if ok:
            return True, message or "已移到回收站。"
    except Exception:
        pass
    try:
        destination = Path(trash_root) / kind / f"{source.name}-{stamp()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        return True, f"已移到开发区回收目录：{destination}"
    except Exception as exc:
        return False, f"删除失败：{exc}"


def redact(text: str, secrets: Iterable[str]) -> str:
    """把已经解析出来的密钥从要返回给模型的文本里抹掉。"""
    value = str(text)
    for secret in secrets:
        secret = str(secret or "")
        if len(secret) >= 4:
            value = value.replace(secret, "***")
    return value


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 这类简单 YAML frontmatter，不引入 PyYAML。

    只支持 `key: value` 和 `key: "quoted"`；metadata 之类的嵌套块会被忽略，
    对技能索引来说够用。
    """
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value.startswith("---\n"):
        return {}, value
    end = value.find("\n---", 4)
    if end < 0:
        return {}, value
    block = value[4:end]
    body_start = end + len("\n---")
    if body_start < len(value) and value[body_start] == "\n":
        body_start += 1
    body = value[body_start:]
    data: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            continue
        item = raw.strip()
        if item.startswith(('"', "'")) and item.endswith(('"', "'")) and len(item) >= 2:
            item = item[1:-1]
        data[key] = item
    return data, body


def compose_front_matter(metadata: dict[str, Any], body: str) -> str:
    lines = ["---"]
    for key in ("name", "description", "license", "compatibility"):
        value = str(metadata.get(key, "") or "").replace("\n", " ").strip()
        if value:
            escaped = value.replace('"', '\\"')
            lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines) + "\n\n" + str(body or "").lstrip("\n").rstrip() + "\n"


def human_size(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
