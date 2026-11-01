from __future__ import annotations

"""主人指令解析。"""

import shlex
from dataclasses import dataclass, field


@dataclass(slots=True)
class Command:
    name: str
    args: list[str] = field(default_factory=list)
    rest: str = ""
    raw: str = ""

    @property
    def arg(self) -> str:
        return " ".join(self.args).strip()


def parse_command(text: str, prefix: str = "/") -> Command | None:
    value = str(text or "").strip()
    if not value:
        return None
    mark = str(prefix or "/").strip() or "/"
    value = value.replace("／", "/")
    if mark != "/" and value.startswith("／"):
        value = "/" + value[1:]
    if not value.startswith(mark):
        return None
    body = value[len(mark) :].strip()
    if not body:
        return None
    parts = body.split(None, 1)
    name = parts[0].strip().lower()
    tail = parts[1].strip() if len(parts) > 1 else ""
    try:
        args = shlex.split(tail)
    except ValueError:
        args = tail.split()
    return Command(name=name, args=args, rest=tail, raw=value)


def help_text(prefix: str = "/") -> str:
    mark = str(prefix or "/").strip() or "/"
    lines = [
        "QQ 桥接指令：",
        f"{mark}status 查看连接和白名单状态",
        f"{mark}persona 查看人格卡；{mark}persona set 文字 修改",
        f"{mark}memory list | add 内容 | del 关键词",
        f"{mark}slang list | add 词=含义 | del 词",
        f"{mark}allow list | add 123456 [备注] | add group:123456 [备注] | del 123456",
        f"{mark}say private:123456 内容 / {mark}say group:123456 内容",
        f"{mark}voice 要读的文字；{mark}voice group:123456 要读的文字",
        f"{mark}qzone 内容 发一条说说（需先在设置里开启）",
        f"{mark}qzone list | del tid",
        f"{mark}compact 立刻整理记忆和黑话",
        f"{mark}check 测试 SnowLuma 连接",
        f"{mark}help 显示这条说明",
    ]
    return "\n".join(lines)
