from __future__ import annotations

"""桌宠能调用的编程工具。

设计原则和写代码时一样：先读后改、最小变更、结果可验证。
所有路径都锁在开发区里；写文件先备份；执行命令、启用新插件默认要主人确认。
"""

import fnmatch
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, Callable

from . import web as web_tools
from .connectors import ConnectorStore
from .history import ChangeJournal, JournalError
from .knowledge import KnowledgeBase
from .net import NetworkError
from .resource_util import ResourceError
from .skills import SkillStore

SELF_PLUGIN_ID = "lfelkeea.self_dev"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_WRITE_BYTES = 1024 * 1024
MAX_PLUGIN_BYTES = 512 * 1024
MAX_TOOL_RESULT = 16000
MAX_SHELL_OUTPUT = 12000
MAX_LIST_ENTRIES = 300
MAX_GLOB_ENTRIES = 200
MAX_GREP_HITS = 100
MAX_FETCH_BYTES = 1024 * 1024

SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vscode",
    ".backups",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd", ".dll", ".exe", ".zip", ".7z", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".onnx", ".bin"}

KNOWN_PERMISSIONS = {
    "pet.say",
    "pet.action",
    "ai.chat",
    "screen.capture",
    "timer",
    "events",
    "settings",
    "storage",
}

DANGEROUS_TOOLS = {
    "run_shell",
    "create_plugin",
    "save_connector",
    "call_connector",
    "mcp_tools",
    "call_mcp_tool",
}
# 删除属于不可逆操作：即使审批模式是 auto，也必须弹窗确认。
ALWAYS_CONFIRM = {"delete_skill", "delete_connector", "delete_knowledge"}
MAX_KNOWLEDGE_IMPORT = 500_000

TOOL_SPECS: dict[str, dict[str, str]] = {
    "list_dir": {
        "usage": 'list_dir(path=".")',
        "desc": "列出目录内容（目录在前，带文件大小）。",
    },
    "read_file": {
        "usage": 'read_file(path, start=1, limit=400)',
        "desc": "读取文本文件，返回带行号的内容；超长时用 start/limit 分段读。",
    },
    "write_file": {
        "usage": 'write_file(path, content)',
        "desc": "写入或覆盖文件；会自动创建目录，覆盖前自动备份旧版本。",
    },
    "edit_file": {
        "usage": 'edit_file(path, old_string, new_string, replace_all=false)',
        "desc": "精确替换文件内容；old_string 必须唯一（或显式 replace_all）。",
    },
    "glob": {
        "usage": 'glob(pattern)',
        "desc": "按通配符查找文件，例如 **/*.py；pattern 相对工作区。",
    },
    "grep": {
        "usage": 'grep(pattern, path=".", glob="", max_results=80)',
        "desc": "用正则搜索文本文件，返回相对路径、行号和该行内容。",
    },
    "run_shell": {
        "usage": 'run_shell(command, timeout=120)',
        "desc": "在工作区根目录执行命令（Windows 优先 Git Bash），返回退出码和输出。",
    },
    "check_python": {
        "usage": 'check_python(path)',
        "desc": "只做 Python 语法检查，不执行代码。",
    },
    "fetch_url": {
        "usage": 'fetch_url(url, max_chars=12000)',
        "desc": "读取网页或接口的文本内容，用于查文档；不能访问本机和内网。",
    },
    "list_plugins": {
        "usage": "list_plugins()",
        "desc": "列出已安装的桌宠插件、版本和启用状态，避免 id 冲突。",
    },
    "read_logs": {
        "usage": "read_logs(lines=120)",
        "desc": "读取桌宠插件日志的末尾，用于排查插件加载失败。",
    },
    "read_reference": {
        "usage": 'read_reference(name="plugin_api")',
        "desc": "读取插件开发参考（plugin_api / agent_notes / resources）。",
    },
    "list_skills": {
        "usage": "list_skills()",
        "desc": "列出本地技能库（id、名称、适用场景）。",
    },
    "read_skill": {
        "usage": 'read_skill(skill_id, resource="")',
        "desc": "加载一个技能的完整 SKILL.md；resource 非空时读取技能里的某个附件。",
    },
    "search_skills": {
        "usage": 'search_skills(query, limit=10)',
        "desc": "按关键词搜索技能正文，找到该用哪个技能。",
    },
    "save_skill": {
        "usage": 'save_skill(skill_id, name, description, body, resources={})',
        "desc": "新建或更新技能；resources 是 {相对路径: 文本}，可放 references/、templates/。",
    },
    "delete_skill": {
        "usage": "delete_skill(skill_id)",
        "desc": "删除技能（会移到回收目录，且必须主人确认）。",
    },
    "list_connectors": {
        "usage": "list_connectors()",
        "desc": "列出 HTTP / MCP 连接器；密钥值会打码。",
    },
    "call_connector": {
        "usage": 'call_connector(connector_id, path="", method="GET", query={}, body=null, headers={})',
        "desc": "调用 HTTP 连接器；完整地址写在 base_url 里，path 只能是相对路径。",
    },
    "mcp_tools": {
        "usage": "mcp_tools(connector_id)",
        "desc": "启动 MCP 连接器并列出它提供的工具和参数结构。",
    },
    "call_mcp_tool": {
        "usage": 'call_mcp_tool(connector_id, tool, arguments={})',
        "desc": "调用 MCP 服务器上的一个工具。",
    },
    "save_connector": {
        "usage": "save_connector(payload)",
        "desc": "新建或更新连接器定义（HTTP 或 MCP），密钥请用 ${ENV_NAME} 引用环境变量。",
    },
    "delete_connector": {
        "usage": "delete_connector(connector_id)",
        "desc": "删除连接器（先备份 connectors.json，且必须主人确认）。",
    },
    "search_knowledge": {
        "usage": 'search_knowledge(query, limit=6)',
        "desc": "检索本地知识库，返回带 [K文档#片段] 引用的片段。",
    },
    "import_knowledge": {
        "usage": 'import_knowledge(title, text, source="", kind="note")',
        "desc": "把一段文本写进知识库；相同 source 和内容会自动去重。",
    },
    "import_knowledge_file": {
        "usage": 'import_knowledge_file(path, title="")',
        "desc": "读取工作区里的文本文件并加入知识库。",
    },
    "import_knowledge_url": {
        "usage": 'import_knowledge_url(url, title="")',
        "desc": "抓取网页正文并加入知识库。",
    },
    "list_knowledge": {
        "usage": "list_knowledge(limit=50)",
        "desc": "列出知识库文档和片段数量。",
    },
    "delete_knowledge": {
        "usage": "delete_knowledge(document_id)",
        "desc": "删除知识库文档（先导出到 .trash，且必须主人确认）。",
    },
    "web_search": {
        "usage": 'web_search(query, limit=6)',
        "desc": "搜索网页，查不熟悉的库、API、报错和开发工具。",
    },
    "search_github": {
        "usage": 'search_github(query, sort="stars", limit=8)',
        "desc": "搜索 GitHub 仓库，找参考实现和现成工具。",
    },
    "pypi_info": {
        "usage": "pypi_info(package)",
        "desc": "查询 PyPI 包的最新版本、摘要、Python 要求和主页。",
    },
    "search_pypi": {
        "usage": "search_pypi(query, limit=8)",
        "desc": "按关键词搜索 PyPI 包。",
    },
    "create_plugin": {
        "usage": 'create_plugin(plugin_id, files, enable=true, merge=true)',
        "desc": "把 files（路径→内容）写成 data/plugins/<id>/，可立即启用并重载。files 必须包含 plugin.json。",
    },
    "ask_user": {
        "usage": "ask_user(question, options=[])",
        "desc": "向主人提问，等待回答；需求不清或要做破坏性操作时使用。",
    },
}


class ToolError(Exception):
    pass


def _clamp(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


class ToolBox:
    def __init__(
        self,
        *,
        workspace: Path,
        dev_dir: Path,
        plugins_dir: Path,
        data_dir: Path,
        references: Path,
        settings: dict[str, Any],
        approver: Callable[[str, str, str, str], bool] | None = None,
        asker: Callable[[str, list[str]], str] | None = None,
        enable_plugin: Callable[[str, bool], tuple[bool, str]] | None = None,
        stop_event: threading.Event | None = None,
        logger: Callable[[str], None] | None = None,
        journal: ChangeJournal | None = None,
        skills: SkillStore | None = None,
        connectors: ConnectorStore | None = None,
        knowledge: KnowledgeBase | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.dev_dir = Path(dev_dir).resolve()
        self.drafts = (self.dev_dir / "drafts").resolve()
        self.drafts.mkdir(parents=True, exist_ok=True)
        self.plugins_dir = Path(plugins_dir).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.references = Path(references).resolve()
        self.settings = dict(settings)
        self.approver = approver
        self.asker = asker
        self.enable_plugin = enable_plugin
        self.stop_event = stop_event or threading.Event()
        self.log = logger or (lambda _message: None)
        self.journal = journal or ChangeJournal(
            self.dev_dir, self.plugins_dir, self.workspace, logger=self.log
        )
        self.changed_files: list[str] = []
        self.trash = (self.dev_dir / ".trash").resolve()
        self.trash.mkdir(parents=True, exist_ok=True)
        self.skills = skills or SkillStore(self.dev_dir / "skills", self.trash)
        self.connectors = connectors or ConnectorStore(
            self.dev_dir / "connectors.json", self.workspace, self.trash
        )
        self.knowledge = knowledge or KnowledgeBase(self.dev_dir / "knowledge")

    # ------------------------------------------------------------------ 入口

    def run(self, name: str, args: Any) -> str:
        spec = TOOL_SPECS.get(str(name or "").strip())
        if spec is None:
            return f"错误：未知工具 {name}。请只使用系统提示里列出的工具。"
        payload = args if isinstance(args, dict) else {}
        if args is not None and not isinstance(args, dict):
            return "错误：args 必须是 JSON 对象。"
        handler = getattr(self, "tool_" + str(name), None)
        if handler is None:
            return f"错误：工具 {name} 尚未实现。"
        try:
            result = handler(**payload)
        except TypeError as exc:
            return f"错误：{name} 的参数不正确（{exc}）。用法：{spec['usage']}"
        except ToolError as exc:
            return f"错误：{exc}"
        except Exception as exc:  # 工具异常只影响这一步
            self.log(f"工具 {name} 异常：{type(exc).__name__}: {exc}")
            return f"错误：{type(exc).__name__}: {exc}"
        text = str(result if result is not None else "")
        if len(text) > MAX_TOOL_RESULT:
            text = text[:MAX_TOOL_RESULT] + f"\n…（结果超过 {MAX_TOOL_RESULT} 字，已截断）"
        return text

    # -------------------------------------------------------------- 路径与安全

    def _resolve(self, raw: Any) -> Path:
        text = str(raw if raw not in (None, "") else ".").strip().replace("\\", "/")
        if text.startswith("~"):
            raise ToolError("只接受相对工作区根目录的路径，不要用 ~。")
        if text.startswith("/") or text.startswith("//") or re.match(r"^[a-zA-Z]:", text):
            raise ToolError("只接受相对工作区根目录的路径，不要用绝对路径或盘符。")
        parts = Path(text).parts
        if any(part == ".." for part in parts):
            raise ToolError("路径里不能出现 ..。")
        if parts and parts[0] == "drafts":
            root = self.drafts
            sub = Path(*parts[1:]) if len(parts) > 1 else Path(".")
        else:
            root = self.workspace
            sub = Path(text)
        resolved = (root / sub).resolve()
        if resolved != root and root not in resolved.parents:
            raise ToolError("这个路径在开发区之外，不能访问。")
        return resolved

    def _rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace).as_posix() or "."
        except ValueError:
            pass
        try:
            return "drafts/" + (path.relative_to(self.drafts).as_posix() or ".")
        except ValueError:
            return path.as_posix()

    def _is_binary(self, path: Path) -> bool:
        try:
            with path.open("rb") as handle:
                return b"\x00" in handle.read(4096)
        except OSError:
            return False

    def _read_text(self, path: Path) -> str:
        raw = path.read_bytes()
        for encoding in ("utf-8-sig", "gb18030", "utf-16", "latin-1"):
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw.decode("utf-8", "replace")

    def _stamp(self) -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"

    def _record_file(self, path: Path) -> None:
        """写入前记下原始版本；记录失败就中止，保证一键还原不会漏。"""
        root = "drafts" if path == self.drafts or self.drafts in path.parents else "workspace"
        try:
            self.journal.record_file(root, path)
        except JournalError as exc:
            raise ToolError(f"没能记录还原基线，已中止这次写入：{exc}") from exc

    def _backup_file(self, path: Path) -> str:
        if not path.exists() or not path.is_file():
            return ""
        rel_text = self._rel(path)
        rel = Path(path.name) if os.path.isabs(rel_text) else Path(rel_text)
        dest = self.dev_dir / ".backups" / "files" / self._stamp() / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        return dest.as_posix()

    def _backup_plugin_folder(self, folder: Path, plugin_id: str) -> str:
        if not folder.is_dir():
            return ""
        dest = self.dev_dir / ".backups" / "plugins" / f"{plugin_id}-{self._stamp()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            folder,
            dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".state"),
        )
        self._prune_backups(plugin_id)
        return dest.as_posix()

    def _prune_backups(self, plugin_id: str, keep: int = 8) -> None:
        root = self.dev_dir / ".backups" / "plugins"
        try:
            folders = sorted(
                [item for item in root.iterdir() if item.is_dir() and item.name.startswith(plugin_id + "-")],
                key=lambda item: item.name,
            )
        except OSError:
            return
        for old in folders[:-keep]:
            try:
                shutil.rmtree(old)
            except OSError:
                pass

    def _needs_approval(self, tool: str) -> bool:
        if tool in ALWAYS_CONFIRM:
            return True
        mode = str(self.settings.get("approval_mode", "confirm_danger"))
        if mode == "auto":
            return False
        if mode == "confirm_all":
            return True
        return tool in DANGEROUS_TOOLS

    def _approve(self, tool: str, title: str, summary: str, detail: str) -> bool:
        if not self._needs_approval(tool):
            return True
        if self.approver is None:
            self.log(f"没有确认界面，已拒绝 {tool}")
            return False
        try:
            return bool(self.approver(tool, title, summary, detail))
        except Exception as exc:
            self.log(f"确认流程异常，按拒绝处理：{exc}")
            return False

    # ------------------------------------------------------------------ 工具

    def tool_list_dir(self, path: str = ".") -> str:
        target = self._resolve(path)
        if not target.exists():
            raise ToolError(f"目录不存在：{path}")
        if not target.is_dir():
            raise ToolError(f"不是目录：{path}")
        entries = sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        lines = [f"目录：{self._rel(target)}"]
        for index, item in enumerate(entries):
            if index >= MAX_LIST_ENTRIES:
                lines.append(f"…（还有 {len(entries) - MAX_LIST_ENTRIES} 项未显示）")
                break
            if item.is_dir():
                lines.append(f"[DIR ] {item.name}/")
            else:
                try:
                    size = item.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"[FILE] {item.name}  ({size} B)")
        if not entries:
            lines.append("（空目录）")
        return "\n".join(lines)

    def tool_read_file(self, path: str, start: int = 1, limit: int = 400) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"文件不存在：{path}")
        size = target.stat().st_size
        if size > MAX_READ_BYTES:
            raise ToolError(f"文件有 {size} B，超过 2 MB；请先用 grep 定位，或分段处理。")
        if self._is_binary(target):
            raise ToolError("这是二进制文件，不能按文本读取。")
        text = self._read_text(target)
        lines = text.splitlines()
        if not lines:
            return f"文件：{self._rel(target)}（空文件，0 行）"
        first = max(1, _clamp(start, 1, 1, 10_000_000))
        count = _clamp(limit, 400, 1, 2000)
        chunk = lines[first - 1 : first - 1 + count]
        if not chunk and lines:
            return f"（start={first} 超出了文件总行数 {len(lines)}）"
        body = "\n".join(f"{first + offset:>5}\t{line}" for offset, line in enumerate(chunk))
        header = f"文件：{self._rel(target)}（共 {len(lines)} 行，显示 {first}-{first + len(chunk) - 1}）"
        remaining = len(lines) - (first - 1 + len(chunk))
        suffix = f"\n…（还有 {remaining} 行，用 start={first + len(chunk)} 继续）" if remaining > 0 else ""
        return f"{header}\n{body}{suffix}"

    def tool_write_file(self, path: str, content: Any) -> str:
        if not isinstance(content, str):
            raise ToolError("content 必须是字符串。")
        target = self._resolve(path)
        data = content.encode("utf-8")
        if len(data) > MAX_WRITE_BYTES:
            raise ToolError(f"内容有 {len(data)} B，超过 1 MB 上限；请拆成多个文件。")
        if not self._approve(
            "write_file",
            "写入文件",
            f"代理要写入 {self._rel(target)}（{len(data)} B）",
            "覆盖前会自动备份旧版本。",
        ):
            return f"主人拒绝了写入 {self._rel(target)}。"
        self._record_file(target)
        backup = self._backup_file(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp-selfdev")
        tmp.write_bytes(data)
        os.replace(tmp, target)
        self.changed_files.append(self._rel(target))
        note = f"；旧版本备份在 {backup}" if backup else ""
        return f"已写入 {self._rel(target)}（{len(data)} B，{len(content.splitlines())} 行）{note}"

    def tool_edit_file(
        self,
        path: str,
        old_string: Any,
        new_string: Any,
        replace_all: bool = False,
    ) -> str:
        if not isinstance(old_string, str) or not old_string:
            raise ToolError("old_string 必须是非空字符串。")
        if not isinstance(new_string, str):
            raise ToolError("new_string 必须是字符串。")
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"文件不存在：{path}")
        text = self._read_text(target)
        occurrences = text.count(old_string)
        if occurrences == 0:
            raise ToolError("没有找到 old_string。请先用 read_file 看当前内容，注意空格和缩进。")
        if occurrences > 1 and not replace_all:
            raise ToolError(
                f"old_string 出现了 {occurrences} 次，无法确定改哪一处；"
                "请多带一些上下文，或显式传 replace_all=true。"
            )
        if not self._approve(
            "edit_file",
            "修改文件",
            f"代理要修改 {self._rel(target)}（替换 {occurrences} 处）",
            f"旧内容长度 {len(old_string)}，新内容长度 {len(new_string)}；覆盖前自动备份。",
        ):
            return f"主人拒绝了修改 {self._rel(target)}。"
        self._record_file(target)
        backup = self._backup_file(target)
        updated = text.replace(old_string, new_string) if replace_all else text.replace(old_string, new_string, 1)
        target.write_bytes(updated.encode("utf-8"))
        self.changed_files.append(self._rel(target))
        index = updated.find(new_string)
        snippet_start = max(0, updated[:index].count("\n") - 2)
        snippet = updated.splitlines()[snippet_start : snippet_start + 6]
        snippet_text = "\n".join(f"{snippet_start + offset + 1:>5}\t{line}" for offset, line in enumerate(snippet))
        note = f"\n旧版本备份：{backup}" if backup else ""
        return f"已修改 {self._rel(target)}（替换 {occurrences} 处）{note}\n修改后附近内容：\n{snippet_text}"

    def tool_glob(self, pattern: str) -> str:
        text = str(pattern or "").strip()
        if not text:
            raise ToolError("pattern 不能为空。")
        if text.startswith("/") or re.match(r"^[a-zA-Z]:", text) or ".." in Path(text).parts:
            raise ToolError("pattern 必须是相对工作区的通配符。")
        matches: list[Path] = []
        try:
            for item in self.workspace.glob(text):
                if item.is_file():
                    matches.append(item)
        except (OSError, ValueError) as exc:
            raise ToolError(f"通配符不合法：{exc}") from exc
        matches.sort(key=lambda item: self._rel(item).lower())
        lines = [self._rel(item) for item in matches[:MAX_GLOB_ENTRIES]]
        suffix = f"\n…（共 {len(matches)} 个，只显示前 {MAX_GLOB_ENTRIES} 个）" if len(matches) > MAX_GLOB_ENTRIES else ""
        return ("\n".join(lines) if lines else "没有找到匹配的文件。") + suffix

    def tool_grep(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "",
        max_results: int = 80,
    ) -> str:
        try:
            regex = re.compile(str(pattern or ""), re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"正则表达式不合法：{exc}") from exc
        root = self._resolve(path)
        if not root.exists():
            raise ToolError(f"路径不存在：{path}")
        limit = _clamp(max_results, 80, 1, MAX_GREP_HITS)
        hits: list[str] = []
        if root.is_file():
            files = [root]
        else:
            files = []
            for base, dirs, names in os.walk(root):
                dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
                files.extend(Path(base) / name for name in names)
        for file_path in files:
            if len(hits) >= limit:
                break
            if not file_path.is_file():
                continue
            rel = self._rel(file_path)
            if glob and not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(file_path.name, glob)):
                continue
            if file_path.suffix.lower() in SKIP_SUFFIXES:
                continue
            try:
                if file_path.stat().st_size > MAX_READ_BYTES or self._is_binary(file_path):
                    continue
                content = self._read_text(file_path)
            except OSError:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                if regex.search(line):
                    hits.append(f"{rel}:{number}: {line.strip()[:300]}")
                    if len(hits) >= limit:
                        break
        return "\n".join(hits) if hits else "没有找到匹配项。"

    def tool_run_shell(self, command: str, timeout: int = 120) -> str:
        text = str(command or "").strip()
        if not text:
            raise ToolError("command 不能为空。")
        mode = str(self.settings.get("shell_mode", "auto"))
        if mode == "off":
            raise ToolError("当前设置禁止执行命令；需要时请主人在设置里打开 shell。")
        if not self._approve(
            "run_shell",
            "执行命令",
            text[:300],
            f"工作目录：{self.workspace}\n超时：{timeout} 秒\n命令输出会原样返回给模型。",
        ):
            return "主人拒绝了执行这条命令。"

        argv = self._shell_argv(text, mode)
        seconds = _clamp(timeout, 120, 5, 600)
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

        started = time.time()
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(self.workspace),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                creationflags=flags,
            )
        except OSError as exc:
            raise ToolError(f"无法启动命令：{exc}") from exc

        reason = {"text": ""}

        def watchdog() -> None:
            deadline = started + seconds
            while proc.poll() is None:
                if self.stop_event.is_set():
                    reason["text"] = "stopped"
                    self._kill(proc)
                    return
                if time.time() > deadline:
                    reason["text"] = "timeout"
                    self._kill(proc)
                    return
                time.sleep(0.15)

        watcher = threading.Thread(target=watchdog, name="selfdev-shell-watch", daemon=True)
        watcher.start()
        try:
            raw, _ = proc.communicate()
        finally:
            watcher.join(timeout=2)

        output = self._decode(raw or b"")
        if len(output) > MAX_SHELL_OUTPUT:
            output = output[: MAX_SHELL_OUTPUT // 2] + "\n…（中间输出省略）…\n" + output[-MAX_SHELL_OUTPUT // 2 :]
        elapsed = time.time() - started
        if reason["text"] == "stopped":
            head = f"已停止（退出码 {proc.returncode}，耗时 {elapsed:.1f}s）"
        elif reason["text"] == "timeout":
            head = f"命令超时（{seconds}s），已终止"
        else:
            head = f"退出码 {proc.returncode}，耗时 {elapsed:.1f}s"
        return f"{head}\n{output.strip() or '（没有输出）'}"

    def _shell_argv(self, command: str, mode: str) -> list[str]:
        if os.name != "nt":
            return ["/bin/sh", "-c", command]
        if mode == "bash":
            bash = shutil.which("bash")
            if not bash:
                raise ToolError("找不到 bash，请把 shell 模式改成 cmd 或 auto。")
            return [bash, "-c", command]
        if mode == "powershell":
            return ["powershell", "-NoProfile", "-NonInteractive", "-Command", command]
        if mode == "cmd":
            return ["cmd", "/d", "/s", "/c", command]
        bash = shutil.which("bash")
        if bash:
            return [bash, "-c", command]
        return ["cmd", "/d", "/s", "/c", command]

    def _kill(self, proc: subprocess.Popen) -> None:
        try:
            proc.kill()
        except OSError:
            pass

    def _decode(self, raw: bytes) -> str:
        for encoding in ("utf-8", "gb18030", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except (UnicodeDecodeError, UnicodeError):
                continue
        return raw.decode("utf-8", "replace")

    def tool_check_python(self, path: str) -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"文件不存在：{path}")
        if target.suffix.lower() != ".py":
            raise ToolError("check_python 只能检查 .py 文件。")
        source = self._read_text(target)
        try:
            compile(source, self._rel(target), "exec")
        except SyntaxError as exc:
            line = exc.text.rstrip("\n") if exc.text else ""
            return (
                f"语法错误：{self._rel(target)} 第 {exc.lineno} 行第 {exc.offset} 列\n"
                f"{line}\n{exc.msg}"
            )
        except ValueError as exc:
            return f"语法检查失败：{exc}"
        return f"语法检查通过：{self._rel(target)}（{len(source.splitlines())} 行）"

    def tool_fetch_url(self, url: str, max_chars: int = 12000) -> str:
        text = str(url or "").strip()
        if not self.settings.get("allow_network", True):
            raise ToolError("当前设置禁止联网；需要时请主人在设置里打开。")
        parsed = urllib.parse.urlparse(text)
        if parsed.scheme not in ("http", "https"):
            raise ToolError("只支持 http/https 地址。")
        host = parsed.hostname or ""
        if not self.settings.get("allow_private_network", False):
            if host.lower() in ("localhost",) or host.endswith(".local"):
                raise ToolError("默认不允许访问本机和内网地址。")
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and (
                address.is_private or address.is_loopback or address.is_link_local or address.is_reserved
            ):
                raise ToolError("默认不允许访问本机和内网地址。")
        request = urllib.request.Request(
            text,
            headers={"User-Agent": "Mozilla/5.0 (LFELKEEA-SelfDev/1.0)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                content_type = str(response.headers.get("Content-Type", ""))
                raw = response.read(MAX_FETCH_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ToolError(f"网页返回 {exc.code}") from exc
        except Exception as exc:
            raise ToolError(f"读取网页失败：{exc}") from exc
        body = self._decode(raw[:MAX_FETCH_BYTES])
        if "html" in content_type.lower() or body.lstrip().lower().startswith("<!doctype html"):
            body = self._strip_html(body)
        limit = _clamp(max_chars, 12000, 500, 20000)
        suffix = f"\n…（内容已截断到 {limit} 字）" if len(body) > limit else ""
        return f"URL：{text}\n类型：{content_type or '未知'}\n\n{body[:limit]}{suffix}"

    def _strip_html(self, html: str) -> str:
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
        text = re.sub(r"(?is)<br\s*/?>", "\n", text)
        text = re.sub(r"(?is)</(p|div|li|h[1-6]|tr)>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = unescape(text)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()

    def tool_list_plugins(self) -> str:
        enabled: dict[str, bool] = {}
        try:
            config = json.loads((self.data_dir / "config.json").read_text(encoding="utf-8"))
            plugins = config.get("plugins") if isinstance(config, dict) else {}
            if isinstance(plugins, dict) and isinstance(plugins.get("enabled"), dict):
                enabled = {str(k): bool(v) for k, v in plugins["enabled"].items()}
        except (OSError, ValueError):
            pass
        lines: list[str] = []
        try:
            folders = sorted(item for item in self.plugins_dir.iterdir() if item.is_dir() and not item.name.startswith("."))
        except OSError:
            folders = []
        for folder in folders:
            manifest_path = folder / "plugin.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                lines.append(f"- {folder.name}（plugin.json 无法解析）")
                continue
            plugin_id = str(manifest.get("id", folder.name))
            state = "已启用" if enabled.get(plugin_id) else "未启用"
            if plugin_id == SELF_PLUGIN_ID:
                state += "（当前插件）"
            description = str(manifest.get("description", ""))[:70]
            lines.append(
                f"- {plugin_id}  v{manifest.get('version', '?')}  [{state}]  {manifest.get('name', '')}"
                + (f"\n    {description}" if description else "")
            )
        return "\n".join(lines) if lines else "还没有安装任何插件。"

    def tool_read_logs(self, lines: int = 120) -> str:
        path = self.data_dir / "plugins.log"
        if not path.is_file():
            return "还没有插件日志。"
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                handle.seek(max(0, size - 128 * 1024))
                raw = handle.read()
        except OSError as exc:
            raise ToolError(f"读取日志失败：{exc}") from exc
        text = self._decode(raw)
        rows = text.splitlines()[-_clamp(lines, 120, 10, 300) :]
        return f"日志：{path.as_posix()}\n" + "\n".join(rows)

    def tool_read_reference(self, name: str = "plugin_api") -> str:
        stem = str(name or "plugin_api").strip()
        stem = re.sub(r"[^a-zA-Z0-9_-]", "", stem)
        if not stem:
            raise ToolError("参考名不合法。")
        path = self.references / f"{stem}.md"
        if not path.is_file():
            available = ", ".join(sorted(item.stem for item in self.references.glob("*.md")))
            raise ToolError(f"没有这份参考：{stem}。可用：{available or '无'}")
        return self._read_text(path)

    # ------------------------------------------------------------------ 资源

    def _require_network(self) -> None:
        if not self.settings.get("allow_network", True):
            raise ToolError("当前设置禁止联网；需要时请主人在设置里打开「允许联网」。")

    def _require_connectors(self) -> None:
        if not self.settings.get("allow_connectors", True):
            raise ToolError("当前设置禁止使用连接器；需要时请主人在设置里打开「允许连接器」。")

    # ---------------------------------------------------------- 技能

    def tool_list_skills(self) -> str:
        return self.skills.index_text()

    def tool_read_skill(self, skill_id: str, resource: str = "") -> str:
        try:
            if str(resource or "").strip():
                return self.skills.read_resource(skill_id, resource)
            skill = self.skills.get(skill_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        extras = [name for name in skill["files"] if name != "SKILL.md"]
        lines = [
            f"技能：{skill['name']}（{skill['id']}）",
            f"适用：{skill['description']}",
            "",
            skill["body"],
        ]
        if extras:
            lines.extend(["", "附件（用 read_skill(skill_id, resource) 读取）：" + "、".join(extras)])
        return "\n".join(lines)

    def tool_search_skills(self, query: str, limit: int = 10) -> str:
        try:
            hits = self.skills.search(query, limit=limit)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        if not hits:
            return f"没有找到和「{query}」相关的技能。可以用 list_skills 看全部。"
        lines = [f"找到 {len(hits)} 个相关技能："]
        for item in hits:
            lines.append(f"- `{item['id']}` — {item['name']}：{item['description']}")
            lines.append(f"  片段：{item['snippet'][:240]}")
        return "\n".join(lines)

    def tool_save_skill(
        self,
        skill_id: str,
        name: str = "",
        description: str = "",
        body: str = "",
        resources: Any = None,
    ) -> str:
        try:
            skill = self.skills.save(skill_id, name, description, body, resources)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        self.changed_files.append(f"data/dev/skills/{skill['id']}")
        extras = [item for item in skill["files"] if item != "SKILL.md"]
        return (
            f"已保存技能 {skill['id']}「{skill['name']}」"
            f"（正文 {len(skill['body'])} 字"
            + (f"，附件：{'、'.join(extras)}" if extras else "")
            + "）。系统提示里的技能索引会在下一轮生效。"
        )

    def tool_delete_skill(self, skill_id: str) -> str:
        try:
            skill = self.skills.get(skill_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        if not self._approve(
            "delete_skill",
            "删除技能",
            f"删除技能「{skill['name']}」（{skill_id}）",
            "技能目录会移到回收目录；需要的话可以从那里找回来。",
        ):
            return f"主人拒绝了删除技能 {skill_id}。"
        try:
            message = self.skills.delete(skill_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        return f"已删除技能 {skill_id}：{message}"

    # ---------------------------------------------------------- 连接器

    @staticmethod
    def _public_connector(item: dict[str, Any]) -> dict[str, Any]:
        return ConnectorStore.public(item)

    def tool_list_connectors(self) -> str:
        items = self.connectors.list()
        if not items:
            return (
                "还没有连接器。\n"
                "HTTP 连接器示例：{\"id\":\"github\",\"type\":\"http\","
                "\"base_url\":\"https://api.github.com\",\"headers\":{\"Authorization\":\"Bearer ${GITHUB_TOKEN}\"}}"
            )
        lines = [f"连接器（{len(items)} 个）："]
        for item in items:
            kind = "HTTP" if item["type"] == "http" else "MCP"
            lines.append(f"- `{item['id']}`（{kind}）{item['name']}：{item.get('description', '')}")
            lines.append(json.dumps(self._public_connector(item), ensure_ascii=False, indent=2))
        return "\n".join(lines)

    def tool_save_connector(self, payload: Any = None, **fields: Any) -> str:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise ToolError(f"payload 不是合法 JSON：{exc}") from exc
        if payload is None:
            payload = fields
        elif isinstance(payload, dict) and fields:
            payload = {**fields, **payload}
        if not isinstance(payload, dict):
            raise ToolError("payload 必须是连接器 JSON 对象。")
        connector_id = str(payload.get("id") or "").strip() or "新连接器"
        if not self._approve(
            "save_connector",
            "保存连接器",
            f"写入连接器 {connector_id}",
            "连接器可以访问外部 API 或启动 MCP 子进程；请确认定义可信。\n"
            "密钥请用 ${ENV_NAME} 引用环境变量，不要明文写进 connectors.json。",
        ):
            return f"主人拒绝了保存连接器 {connector_id}。"
        try:
            saved = self.connectors.save(payload)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        self.changed_files.append("data/dev/connectors.json")
        return (
            f"已保存连接器 {saved['id']}（{saved['type']}）。\n"
            + json.dumps(self._public_connector(saved), ensure_ascii=False, indent=2)
            + "\n下一轮任务会看到这个连接器。"
        )

    def tool_delete_connector(self, connector_id: str) -> str:
        try:
            item = self.connectors.get(connector_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        if not self._approve(
            "delete_connector",
            "删除连接器",
            f"删除连接器「{item['name']}」（{connector_id}）",
            "connectors.json 会先备份成 .bak，再移除这条定义。",
        ):
            return f"主人拒绝了删除连接器 {connector_id}。"
        try:
            message = self.connectors.delete(connector_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        self.changed_files.append("data/dev/connectors.json")
        return message

    def tool_call_connector(
        self,
        connector_id: str,
        path: str = "",
        method: str = "GET",
        query: Any = None,
        body: Any = None,
        headers: Any = None,
    ) -> str:
        self._require_network()
        self._require_connectors()
        if not self._approve(
            "call_connector",
            "调用 HTTP 连接器",
            f"{method.upper()} {connector_id} {path or '/'}",
            "请求会带上前一步保存的 header（包括从环境变量解析的密钥）。\n"
            "返回内容会交给模型，密钥会被打码。",
        ):
            return f"主人拒绝了调用连接器 {connector_id}。"
        try:
            result = self.connectors.http_call(
                connector_id,
                path=path,
                method=method,
                query=query,
                body=body,
                headers=headers,
                allow_private_network=bool(self.settings.get("allow_private_network", False)),
            )
        except (ResourceError, NetworkError) as exc:
            raise ToolError(str(exc)) from exc
        return (
            f"连接器 {connector_id} · HTTP {result['status']} · {result['content_type'] or '未知类型'}\n"
            f"URL：{result['url']}\n\n{result['text']}"
        )

    def tool_mcp_tools(self, connector_id: str) -> str:
        self._require_connectors()
        if not self._approve(
            "mcp_tools",
            "读取 MCP 工具",
            f"启动 MCP 连接器 {connector_id} 并列出工具",
            "会运行连接器配置里的 command；只启动本次需要的子进程，结束后关闭。",
        ):
            return f"主人拒绝了启动 MCP 连接器 {connector_id}。"
        try:
            payload = self.connectors.mcp_tools(connector_id)
        except (ResourceError, NetworkError) as exc:
            raise ToolError(str(exc)) from exc
        tools = payload.get("tools") or []
        server = payload.get("server") or {}
        lines = [
            f"MCP 连接器 {connector_id}"
            + (f" · {server.get('name', '')} {server.get('version', '')}".rstrip() if server else "")
            + f" · {len(tools)} 个工具"
        ]
        for item in tools:
            schema = item.get("inputSchema") or {}
            lines.append(f"- `{item.get('name', '')}`：{item.get('description', '')}")
            lines.append("  参数：" + json.dumps(schema, ensure_ascii=False)[:1200])
        return "\n".join(lines)

    def tool_call_mcp_tool(self, connector_id: str, tool: str, arguments: Any = None) -> str:
        self._require_connectors()
        name = str(tool or "").strip()
        if not name:
            raise ToolError("tool 不能为空。")
        if not isinstance(arguments, dict):
            arguments = {}
        if not self._approve(
            "call_mcp_tool",
            "调用 MCP 工具",
            f"{connector_id} / {name}",
            "参数：\n" + json.dumps(arguments, ensure_ascii=False)[:1500],
        ):
            return f"主人拒绝了调用 MCP 工具 {name}。"
        try:
            result = self.connectors.mcp_call(connector_id, name, arguments)
        except (ResourceError, NetworkError) as exc:
            raise ToolError(str(exc)) from exc
        text = result.get("text", "")
        stderr = str(result.get("stderr") or "").strip()
        if stderr:
            text += f"\n\nMCP 服务器 stderr：\n{stderr[-1200:]}"
        return text

    # ---------------------------------------------------------- 知识库

    def tool_search_knowledge(self, query: str, limit: int = 6) -> str:
        try:
            hits = self.knowledge.search(query, limit=limit)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        if not hits:
            return f"知识库里没有找到「{query}」。可以换关键词，或先用 import_knowledge 存资料。"
        lines = [f"知识库命中 {len(hits)} 条（引用格式 [K文档#片段]）："]
        for item in hits:
            lines.append(
                f"{item['citation']} {item['title']}"
                + (f"（{item['source']}）" if item["source"] else "")
            )
            lines.append(item["snippet"])
            lines.append("")
        return "\n".join(lines).rstrip()

    def tool_import_knowledge(
        self,
        title: str,
        text: str,
        source: str = "",
        kind: str = "note",
    ) -> str:
        body = str(text or "")
        if len(body) > MAX_KNOWLEDGE_IMPORT:
            raise ToolError(f"内容超过 {MAX_KNOWLEDGE_IMPORT} 字，请拆成多次导入。")
        try:
            result = self.knowledge.add_document(title, body, source=source, kind=kind)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        state = "已存在，未重复写入" if result["existing"] else "已写入"
        return f"{state}知识库文档 #{result['id']}「{result['title']}」，共 {result['chunks']} 个片段。"

    def tool_import_knowledge_file(self, path: str, title: str = "") -> str:
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"文件不存在：{path}")
        if target.stat().st_size > 2 * 1024 * 1024:
            raise ToolError("文件超过 2 MB，请先提取需要的部分。")
        if self._is_binary(target):
            raise ToolError("这个文件看起来是二进制，知识库只收文本；请先提取正文。")
        text = self._read_text(target)
        return self.tool_import_knowledge(
            title or target.stem, text, source=self._rel(target), kind="file"
        )

    def tool_import_knowledge_url(self, url: str, title: str = "") -> str:
        self._require_network()
        try:
            page_title, text = web_tools.fetch_readable(url, max_chars=MAX_KNOWLEDGE_IMPORT)
        except NetworkError as exc:
            raise ToolError(str(exc)) from exc
        if not text.strip():
            raise ToolError("网页没有可读正文。")
        return self.tool_import_knowledge(
            title or page_title or url, text, source=url, kind="web"
        )

    def tool_list_knowledge(self, limit: int = 50) -> str:
        items = self.knowledge.list_documents(limit=limit)
        stats = self.knowledge.stats()
        if not items:
            return "知识库还是空的。可以用 import_knowledge / import_knowledge_file / import_knowledge_url 添加资料。"
        lines = [
            f"知识库：{stats['documents']} 个文档，{stats['chunks']} 个片段，约 {stats['chars']} 字。"
        ]
        for item in items:
            lines.append(
                f"#{item['id']} {item['title']} · {item['chunks']} 片 · {item['kind']}"
                + (f" · {item['source']}" if item["source"] else "")
            )
        return "\n".join(lines)

    def tool_delete_knowledge(self, document_id: int) -> str:
        try:
            document = self.knowledge.get_document(document_id)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        if not self._approve(
            "delete_knowledge",
            "删除知识库文档",
            f"删除 #{document['id']}「{document['title']}」",
            "删除前会先把正文导出到 data/dev/.trash/knowledge/，需要时可以找回。",
        ):
            return f"主人拒绝了删除知识库文档 #{document_id}。"
        try:
            title, archive = self.knowledge.archive_and_delete(document_id, self.trash)
        except ResourceError as exc:
            raise ToolError(str(exc)) from exc
        return f"已删除知识库文档 #{document_id}「{title}」，正文已备份到 {archive}。"

    # ---------------------------------------------------------- 联网检索

    def tool_web_search(self, query: str, limit: int = 6) -> str:
        self._require_network()
        try:
            return web_tools.web_search(query, limit=limit)
        except NetworkError as exc:
            raise ToolError(str(exc)) from exc

    def tool_search_github(self, query: str, sort: str = "stars", limit: int = 8) -> str:
        self._require_network()
        try:
            return web_tools.search_github(query, limit=limit, sort=sort)
        except NetworkError as exc:
            raise ToolError(str(exc)) from exc

    def tool_pypi_info(self, package: str) -> str:
        self._require_network()
        try:
            return web_tools.pypi_info(package)
        except NetworkError as exc:
            raise ToolError(str(exc)) from exc

    def tool_search_pypi(self, query: str, limit: int = 8) -> str:
        self._require_network()
        try:
            return web_tools.search_pypi(query, limit=limit)
        except NetworkError as exc:
            raise ToolError(str(exc)) from exc

    def tool_create_plugin(
        self,
        plugin_id: str,
        files: Any,
        enable: bool = True,
        merge: bool = True,
    ) -> str:
        pid = str(plugin_id or "").strip()
        if not ID_RE.match(pid):
            raise ToolError("plugin_id 只能用小写字母、数字、点、下划线、短横线，2-64 位。")
        if pid == SELF_PLUGIN_ID and not self.settings.get("allow_self_modify", False):
            raise ToolError("默认不允许修改自开发工坊自身；需要时请主人打开「允许修改自身」。")
        normalized = self._normalize_plugin_files(files)
        target = (self.plugins_dir / pid).resolve()
        if self.plugins_dir not in target.parents:
            raise ToolError("插件目录越界。")
        existing = target.is_dir()

        manifest_text = normalized.get("plugin.json")
        if manifest_text is None:
            manifest_path = target / "plugin.json"
            if not (merge and manifest_path.is_file()):
                raise ToolError("files 里必须包含 plugin.json。")
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ToolError(f"现有 plugin.json 无法解析：{exc}") from exc
        else:
            try:
                manifest = json.loads(manifest_text)
            except ValueError as exc:
                raise ToolError(f"plugin.json 不是合法 JSON：{exc}") from exc
        if not isinstance(manifest, dict):
            raise ToolError("plugin.json 顶层必须是对象。")
        if str(manifest.get("id", "")).strip() != pid:
            raise ToolError("plugin.json 里的 id 必须和 plugin_id 完全一致。")
        try:
            api_version = int(manifest.get("api_version", 1))
        except (TypeError, ValueError) as exc:
            raise ToolError("api_version 必须是整数 1。") from exc
        if api_version != 1:
            raise ToolError("只支持 api_version=1。")

        entry = str(manifest.get("entry", "main.py") or "main.py").strip()
        entry_path = (target / entry).resolve()
        if target not in entry_path.parents:
            raise ToolError("entry 不能指向插件目录之外。")
        if entry in normalized:
            entry_source = normalized[entry]
        elif (entry_path).is_file():
            entry_source = self._read_text(entry_path)
        else:
            raise ToolError(f"找不到入口文件：{entry}（files 里要提供它）。")
        try:
            compile(entry_source, entry, "exec")
        except SyntaxError as exc:
            raise ToolError(f"入口文件有语法错误：{entry} 第 {exc.lineno} 行：{exc.msg}") from exc

        warnings: list[str] = []
        permissions = manifest.get("permissions", [])
        if isinstance(permissions, list):
            unknown = [item for item in permissions if str(item) not in KNOWN_PERMISSIONS]
            if unknown:
                warnings.append(f"未识别的权限（宿主会忽略）：{', '.join(map(str, unknown))}")
        if not manifest.get("permissions"):
            warnings.append("plugin.json 没声明 permissions；插件只能使用 api.log()。")
        if existing and not merge:
            warnings.append("当前版本只做增量覆盖，不会删除旧文件；不需要的旧文件请主人手动处理。")
        if enable and not self.settings.get("auto_enable_plugin", True):
            enable = False
            warnings.append("设置里关闭了自动启用：插件已写入但未启用。")

        if not self._approve(
            "create_plugin",
            "安装插件" if not existing else "更新插件",
            f"{pid} v{manifest.get('version', '?')}，{len(normalized)} 个文件"
            + ("，完成后自动启用" if enable else "，先不启用"),
            "文件：\n" + "\n".join(f"- {name}" for name in sorted(normalized)),
        ):
            return f"主人拒绝了写入插件 {pid}。"

        if pid != SELF_PLUGIN_ID:
            try:
                self.journal.record_plugin(pid, target)
            except JournalError as exc:
                raise ToolError(f"没能记录插件还原基线，已中止这次安装：{exc}") from exc
        backup = self._backup_plugin_folder(target, pid) if existing else ""
        target.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for rel, content in normalized.items():
            destination = (target / rel).resolve()
            if destination != target and target not in destination.parents:
                raise ToolError(f"文件路径越界：{rel}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp = destination.with_name(destination.name + ".tmp-selfdev")
            tmp.write_bytes(content.encode("utf-8"))
            os.replace(tmp, destination)
            written.append(rel)
        self.changed_files.append(f"data/plugins/{pid}")

        lines = [
            f"已{'更新' if existing else '创建'}插件 {pid} v{manifest.get('version', '?')}",
            "写入文件：" + "、".join(sorted(written)),
        ]
        if backup:
            lines.append(f"旧版本备份：{backup}")
        if warnings:
            lines.append("提示：" + "；".join(warnings))

        if enable:
            if self.enable_plugin is None:
                lines.append("当前环境不能自动启用，请到「插件管理」手动勾选。")
            else:
                ok, message = self.enable_plugin(pid, True)
                lines.append(("已启用并重载：" if ok else "启用失败：") + message)
        else:
            lines.append("未启用；需要时在「插件管理」里勾选。")
        return "\n".join(lines)

    def _normalize_plugin_files(self, files: Any) -> dict[str, str]:
        if isinstance(files, list):
            mapping: dict[str, str] = {}
            for item in files:
                if not isinstance(item, dict):
                    raise ToolError("files 列表里的每一项都要是 {path, content}。")
                path = item.get("path") or item.get("name") or item.get("file")
                if not path:
                    raise ToolError("files 列表里有缺少 path 的项。")
                mapping[str(path)] = item.get("content", "")
            files = mapping
        if not isinstance(files, dict) or not files:
            raise ToolError("files 必须是非空对象（路径 → 文件内容）。")
        normalized: dict[str, str] = {}
        total = 0
        for raw_path, content in files.items():
            rel = str(raw_path or "").strip().replace("\\", "/")
            if rel.startswith("./"):
                rel = rel[2:]
            if not rel or rel.startswith("/") or re.match(r"^[a-zA-Z]:", rel) or ".." in Path(rel).parts:
                raise ToolError(f"插件文件路径不合法：{raw_path}")
            if not isinstance(content, str):
                raise ToolError(f"{rel} 的内容必须是字符串。")
            total += len(content.encode("utf-8"))
            normalized[rel] = content
        if total > MAX_PLUGIN_BYTES:
            raise ToolError(f"插件代码共 {total} B，超过 {MAX_PLUGIN_BYTES} B 上限；请精简或拆包。")
        return normalized

    def tool_ask_user(self, question: str, options: Any = None) -> str:
        text = str(question or "").strip()
        if not text:
            raise ToolError("question 不能为空。")
        choices = [str(item) for item in options] if isinstance(options, list) else []
        if self.asker is None:
            return "（主人现在没有打开工作台，无法回答。请按最保守的方式继续，并在总结里说明这个待确认点。）"
        try:
            answer = self.asker(text, choices)
        except Exception as exc:
            return f"（询问失败：{exc}。请按最保守的方式继续。）"
        return str(answer or "（主人没有回答）")
