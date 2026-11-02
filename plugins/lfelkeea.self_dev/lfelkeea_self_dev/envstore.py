from __future__ import annotations

"""本地环境变量仓库。

连接器里的 ``${ENV_NAME}`` 默认读进程环境；没有系统变量时，再从
``data/dev/env.json`` 取。界面只显示「已配置 / 缺失」和来源，不把值交给
模型、日志或会话记录。

文件里是明文（和桌宠自己的 config.json 一样），所以：

- 文件权限尽量收紧到 600；
- 不进插件包、不进 git、不进知识库；
- 删除本地值时回退到系统环境变量，而不是把系统变量改掉。
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

from .resource_util import ResourceError, atomic_write, read_text

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV_VALUE = 16_000


class EnvStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 读写

    def _load(self) -> dict[str, dict[str, str]]:
        if not self.path.is_file():
            return {}
        try:
            data = json.loads(read_text(self.path))
        except (OSError, ValueError) as exc:
            raise ResourceError(f"env.json 无法解析：{exc}") from exc
        values = data.get("vars") if isinstance(data, dict) else None
        if not isinstance(values, dict):
            return {}
        result: dict[str, dict[str, str]] = {}
        for raw_name, raw in values.items():
            name = str(raw_name or "").strip()
            if not ENV_NAME_RE.match(name):
                continue
            if isinstance(raw, dict):
                value = str(raw.get("value", "") or "")
                note = str(raw.get("note", "") or "")[:200]
            else:
                value = str(raw or "")
                note = ""
            if value:
                result[name] = {"value": value, "note": note}
        return result

    def _save(self, values: dict[str, dict[str, str]]) -> None:
        payload = {
            "version": 1,
            "note": "本地环境变量。只供本机自开发工坊使用；不要提交或分享这个文件。",
            "vars": values,
        }
        atomic_write(self.path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    # ------------------------------------------------------------ 查询

    @staticmethod
    def valid_name(name: str) -> bool:
        return bool(ENV_NAME_RE.match(str(name or "").strip()))

    def get(self, name: str) -> str:
        """本地值优先；没有本地值时返回空串，调用方再回退系统环境。"""
        key = str(name or "").strip()
        if not self.valid_name(key):
            return ""
        try:
            item = self._load().get(key)
        except ResourceError:
            return ""
        return str(item.get("value", "")) if item else ""

    def source(self, name: str) -> str:
        key = str(name or "").strip()
        if not self.valid_name(key):
            return "invalid"
        if self.get(key):
            return "local"
        if os.environ.get(key):
            return "system"
        return "missing"

    def is_configured(self, name: str) -> bool:
        return self.source(name) in ("local", "system")

    def names(self) -> list[str]:
        try:
            return sorted(self._load())
        except ResourceError:
            return []

    def public(self, required: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """给界面看的清单：只有名字、来源和状态，绝不带值。"""
        try:
            local = self._load()
        except ResourceError:
            local = {}
        names = sorted(set(required or []) | set(local))
        result: list[dict[str, Any]] = []
        for name in names:
            if not self.valid_name(name):
                continue
            source = self.source(name)
            result.append(
                {
                    "name": name,
                    "configured": source in ("local", "system"),
                    "source": source,
                    "masked": "***" if source in ("local", "system") else "",
                    "note": str((local.get(name) or {}).get("note", "")),
                }
            )
        return result

    # ------------------------------------------------------------ 写入

    def set(self, name: str, value: str, note: str = "") -> None:
        self.set_many({name: value}, note=note)

    def set_many(self, values: dict[str, Any], note: str = "") -> int:
        if not isinstance(values, dict):
            raise ResourceError("环境变量必须是 {NAME: value} 对象。")
        current = self._load()
        changed = 0
        for raw_name, raw_value in values.items():
            name = str(raw_name or "").strip()
            if not self.valid_name(name):
                raise ResourceError(f"环境变量名不合法：{raw_name}")
            value = str(raw_value if raw_value is not None else "")
            if len(value) > MAX_ENV_VALUE:
                raise ResourceError(f"环境变量 {name} 的值超过 {MAX_ENV_VALUE} 字符。")
            if value == "":
                current.pop(name, None)
            else:
                current[name] = {"value": value, "note": str(note or "")[:200]}
            changed += 1
        if changed:
            self._save(current)
        return changed

    def clear(self, name: str) -> bool:
        key = str(name or "").strip()
        current = self._load()
        if key not in current:
            return False
        current.pop(key, None)
        self._save(current)
        return True

    # ------------------------------------------------------------ 激活

    def activate(self) -> "EnvStore":
        """让 net.resolve_env 优先读这个仓库。"""
        from .net import set_env_provider

        set_env_provider(self.get)
        return self
