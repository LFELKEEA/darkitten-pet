from __future__ import annotations

"""检查技能和连接器「能不能真的用」。

- 连接器：扫 ``${ENV_NAME}``、检查 MCP 启动命令是否存在；
- 技能：读 frontmatter 的 requires_env / requires_commands，以及
  ``requirements.json`` 里的 env / commands / downloads；
- 只返回名称、状态和帮助链接，不返回值；
- 准备资源时只生成明确的命令和下载清单，执行前必须由调用方确认。
"""

import os
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .net import ENV_RE, NetworkError, download_bytes
from .resource_util import ResourceError, inside, safe_relative

MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
MAX_DOWNLOAD_TOTAL = 8 * 1024 * 1024
MAX_PREPARE_OUTPUT = 12_000

# 老版本已经写入本地技能库的条目没有新增的 frontmatter 字段；
# 用 id 兜底，保证升级后仍然能检查出依赖。
SKILL_HINTS: dict[str, dict[str, list[str]]] = {
    "github-rest-api": {"env": ["GITHUB_PAT"]},
    "feishu-open-api": {"env": ["FEISHU_ACCESS_TOKEN"]},
    "notion-api": {"env": ["NOTION_TOKEN"]},
    "sql-readonly-gateway": {"env": ["SQL_GATEWAY_URL", "SQL_GATEWAY_TOKEN"]},
    "searxng-search": {"env": ["SEARXNG_URL"]},
    "web-page-fetch": {"env": ["FETCH_GATEWAY_URL"]},
    "mcp-toolkit": {"commands": ["npx", "uvx"]},
    "three-d-modeling": {"commands": ["blender"]},
}

ENV_DOCS: dict[str, dict[str, Any]] = {
    "GITHUB_PAT": {
        "label": "GitHub 个人访问令牌",
        "help_url": "https://github.com/settings/tokens",
        "secret": True,
        "note": "只读仓库给 repo:status、public_repo；要写 issue 才需要 issues 权限。",
    },
    "GITHUB_PERSONAL_ACCESS_TOKEN": {
        "label": "GitHub 个人访问令牌（MCP）",
        "help_url": "https://github.com/settings/tokens",
        "secret": True,
        "note": "MCP GitHub 服务器使用；权限按实际需要最小化。",
    },
    "GITLAB_TOKEN": {
        "label": "GitLab 访问令牌",
        "help_url": "https://gitlab.com/-/user_settings/personal_access_tokens",
        "secret": True,
        "note": "只读项目选 read_api；需要写 issue / MR 时再加权限。",
    },
    "FEISHU_ACCESS_TOKEN": {
        "label": "飞书 tenant_access_token",
        "help_url": "https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal",
        "secret": True,
        "note": "约 2 小时过期，最好在平台侧自动刷新后再写入。",
    },
    "NOTION_TOKEN": {
        "label": "Notion 集成令牌",
        "help_url": "https://www.notion.so/my-integrations",
        "secret": True,
        "note": "集成需要被邀请到目标页面或数据库，否则会 404。",
    },
    "SEARXNG_URL": {
        "label": "SearXNG 实例地址",
        "help_url": "https://docs.searxng.org/admin/installation.html",
        "secret": False,
        "note": "填完整 http(s) 地址，例如 https://search.example.com。",
    },
    "SQL_GATEWAY_URL": {
        "label": "只读 SQL 网关地址",
        "help_url": "",
        "secret": False,
        "note": "网关侧必须强制只允许 SELECT。",
    },
    "SQL_GATEWAY_TOKEN": {
        "label": "SQL 网关令牌",
        "help_url": "",
        "secret": True,
        "note": "只保存在本机 env.json 或系统环境变量里。",
    },
    "FETCH_GATEWAY_URL": {
        "label": "网页转 Markdown 网关地址",
        "help_url": "",
        "secret": False,
        "note": "填完整 http(s) 地址；按你的服务约定传 url 参数。",
    },
    "BRAVE_API_KEY": {
        "label": "Brave Search API Key",
        "help_url": "https://api.search.brave.com/app/keys",
        "secret": True,
        "note": "免费额度有限，注意调用频率。",
    },
    "OPENAI_API_KEY": {
        "label": "OpenAI / 兼容网关 API Key",
        "help_url": "https://platform.openai.com/api-keys",
        "secret": True,
        "note": "使用第三方兼容网关时，只填该网关签发的 Key，并同步修改连接器 base_url。",
    },
    "TAVILY_API_KEY": {
        "label": "Tavily API Key",
        "help_url": "https://app.tavily.com/home",
        "secret": True,
        "note": "免费额度有限；搜索请求按次数计费，注意调用频率。",
    },
}

RUNTIME_DOCS: dict[str, dict[str, str]] = {
    "npx": {
        "label": "Node.js（npm / npx）",
        "help_url": "https://nodejs.org/zh-cn/download",
        "note": "装 LTS 版本即可；装完重开工作台让 PATH 生效。",
    },
    "npm": {
        "label": "Node.js（npm）",
        "help_url": "https://nodejs.org/zh-cn/download",
        "note": "装 LTS 版本即可。",
    },
    "node": {
        "label": "Node.js",
        "help_url": "https://nodejs.org/zh-cn/download",
        "note": "装 LTS 版本即可。",
    },
    "uvx": {
        "label": "uv（uvx）",
        "help_url": "https://docs.astral.sh/uv/getting-started/installation/",
        "note": "Windows 可用 winget install --id=astral-sh.uv -e。",
    },
    "uv": {
        "label": "uv",
        "help_url": "https://docs.astral.sh/uv/getting-started/installation/",
        "note": "Windows 可用 winget install --id=astral-sh.uv -e。",
    },
    "blender": {
        "label": "Blender",
        "help_url": "https://www.blender.org/download/",
        "note": "还需要在 Blender 里启用 blender-mcp addon。",
    },
    "git": {
        "label": "Git",
        "help_url": "https://git-scm.com/downloads",
        "note": "Windows 安装时保留「Git Bash」选项。",
    },
    "python": {
        "label": "Python",
        "help_url": "https://www.python.org/downloads/",
        "note": "安装时勾选 Add Python to PATH。",
    },
}


def env_names_in(value: Any) -> list[str]:
    if isinstance(value, dict):
        return env_names_in(list(value.values()))
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        for item in value:
            names.extend(env_names_in(item))
        return names
    if not isinstance(value, str):
        return []
    return [match.group(1) for match in ENV_RE.finditer(value)]


def connector_env_names(connector: dict[str, Any]) -> list[str]:
    names: list[str] = []
    if connector.get("type") == "http":
        names.extend(env_names_in(connector.get("base_url")))
        names.extend(env_names_in(connector.get("headers") or {}))
    else:
        names.extend(env_names_in(connector.get("env") or {}))
        # args 目前不解析环境变量；这里只报告确实会被解析的位置。
    return sorted(set(names))


_PATHEXT_FALLBACK = ".COM;.EXE;.BAT;.CMD"


def _registry_path() -> str:
    """读注册表里的 Machine + User PATH；非 Windows 或读取失败返回空串。"""
    if os.name != "nt":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    parts: list[str] = []
    for root, sub in (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, "Environment"),
    ):
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        if value:
            parts.append(str(value))
    return os.pathsep.join(parts)


def merged_path() -> str:
    """合并当前进程 PATH 和注册表 PATH。

    桌宠启动后新装的工具不会自动进入它已有的 PATH；重新读注册表，
    依赖检查和子进程就能立刻看到新命令，不用重启桌宠。
    """
    seen: set[str] = set()
    parts: list[str] = []
    for source in (os.environ.get("PATH", ""), _registry_path()):
        for raw in str(source or "").split(os.pathsep):
            part = os.path.expandvars(raw.strip().strip('"'))
            if not part:
                continue
            key = part.rstrip("\\/").lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(part)
    return os.pathsep.join(parts)


def _extensions() -> list[str]:
    if os.name != "nt":
        return [""]
    raw = os.environ.get("PATHEXT") or _PATHEXT_FALLBACK
    exts = [item.strip().lower() for item in raw.split(";") if item.strip()]
    return exts or [".exe"]


def _uvx_needs_sibling_uv(path: Path) -> bool:
    """uvx 依赖同目录的 uv.exe；缺 uv 的旧包装器会直接报错。"""
    if os.name != "nt" or path.stem.lower() != "uvx":
        return False
    return not (path.parent / "uv.exe").is_file()


def resolve_command(command: str) -> str:
    """返回命令的绝对路径；找不到返回空串。

    查找范围包含启动后新写入注册表、但当前进程还看不到的 PATH。
    """
    value = str(command or "").strip().strip('"')
    if not value:
        return ""
    direct = Path(value)
    if direct.is_file():
        return str(direct)
    for directory in merged_path().split(os.pathsep):
        if not directory:
            continue
        for ext in _extensions():
            candidate = Path(directory) / (value + ext)
            if not candidate.is_file():
                continue
            if _uvx_needs_sibling_uv(candidate):
                continue
            return str(candidate)
    return ""


def command_env(*, base: dict[str, str] | None = None) -> dict[str, str]:
    """子进程环境：PATH 用刷新后的值，并统一 UTF-8 输出。"""
    env = dict(os.environ if base is None else base)
    path = merged_path()
    if path:
        env["PATH"] = path
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def command_available(command: str) -> bool:
    return bool(resolve_command(command))


def runtime_doc(command: str) -> dict[str, str]:
    name = Path(str(command or "").strip()).name.lower()
    if name.endswith(".exe"):
        name = name[:-4]
    doc = RUNTIME_DOCS.get(name)
    if doc:
        return {"label": doc["label"], "help_url": doc["help_url"], "note": doc["note"]}
    return {"label": name or "命令", "help_url": "", "note": "确认已安装并加入 PATH。"}


def _env_requirement(name: str, store: Any) -> dict[str, Any]:
    doc = ENV_DOCS.get(name, {})
    source = store.source(name) if store is not None else "missing"
    configured = source in ("local", "system")
    return {
        "kind": "env",
        "name": name,
        "label": str(doc.get("label") or name),
        "configured": configured,
        "source": source,
        "secret": bool(doc.get("secret", True)),
        "help_url": str(doc.get("help_url") or ""),
        "note": str(doc.get("note") or ""),
        "masked": "***" if configured else "",
    }


def _command_requirement(command: str) -> dict[str, Any]:
    doc = runtime_doc(command)
    available = command_available(command)
    return {
        "kind": "command",
        "name": str(command),
        "label": doc["label"],
        "configured": available,
        "source": "system" if available else "missing",
        "secret": False,
        "help_url": doc["help_url"],
        "note": doc["note"],
        "masked": "",
    }


def _skill_requirements(skill: dict[str, Any]) -> dict[str, Any]:
    sidecar = skill.get("requirements") if isinstance(skill.get("requirements"), dict) else {}
    hint = SKILL_HINTS.get(str(skill.get("id") or ""), {})
    env = (
        list(skill.get("requires_env") or [])
        + list(sidecar.get("env") or [])
        + list(hint.get("env") or [])
    )
    commands = (
        list(skill.get("requires_commands") or [])
        + list(sidecar.get("commands") or [])
        + list(hint.get("commands") or [])
    )
    downloads = sidecar.get("downloads") if isinstance(sidecar.get("downloads"), list) else []
    return {
        "env": sorted({str(item) for item in env if str(item).strip()}),
        "commands": sorted({str(item) for item in commands if str(item).strip()}),
        "downloads": [item for item in downloads if isinstance(item, dict)],
    }


def item_requirements(kind: str, item: dict[str, Any], env_store: Any) -> dict[str, Any]:
    if kind == "connector":
        env = connector_env_names(item)
        commands: list[str] = []
        if item.get("type") == "mcp":
            commands.append(str(item.get("command") or ""))
        downloads: list[dict[str, Any]] = []
    else:
        declared = _skill_requirements(item)
        env = declared["env"]
        commands = declared["commands"]
        downloads = declared["downloads"]
    requirements = [_env_requirement(name, env_store) for name in env]
    requirements.extend(
        _command_requirement(command) for command in commands if str(command or "").strip()
    )
    missing = [item for item in requirements if not item["configured"]]
    return {
        "kind": kind,
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "enabled": bool(item.get("enabled")),
        "requirements": requirements,
        "missing": len(missing),
        "ready": not missing,
        "downloads": downloads,
    }


def check_items(skills: Any, connectors: Any, env_store: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for connector in connectors.list():
        items.append(item_requirements("connector", connector, env_store))
    for listed in skills.list():
        try:
            detail = skills.get(listed["id"])
        except ResourceError:
            continue
        items.append(item_requirements("skill", detail, env_store))
    items.sort(key=lambda item: (not item["enabled"], item["missing"] == 0, item["kind"], item["name"]))
    return items


def status_text(status: dict[str, Any]) -> str:
    if not status.get("enabled"):
        prefix = "已关闭"
    elif status.get("ready"):
        prefix = "就绪"
    else:
        prefix = f"缺 {status.get('missing', 0)} 项"
    return prefix


def _package_for(connector: dict[str, Any]) -> str:
    command = Path(str(connector.get("command") or "")).name.lower()
    if command.endswith(".exe"):
        command = command[:-4]
    if command not in ("npx", "uvx"):
        return ""
    for token in connector.get("args") or []:
        text = str(token)
        if not text or text.startswith("-"):
            continue
        return text
    return ""


def _command(action: str, argv: list[str], note: str = "") -> dict[str, Any]:
    return {"action": action, "argv": [str(part) for part in argv], "note": note}


def prepare_plan(kind: str, item: dict[str, Any], env_store: Any) -> dict[str, Any]:
    """生成准备清单；不执行。"""
    commands: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    links: list[dict[str, str]] = []
    notes: list[str] = []
    blocked: list[str] = []

    if kind == "connector":
        if item.get("type") == "mcp":
            command = str(item.get("command") or "").strip()
            runtime = runtime_doc(command)
            if not command_available(command):
                blocked.append(f"缺少启动命令 {command}，先安装 {runtime['label']}。")
                if runtime["help_url"]:
                    links.append(
                        {
                            "label": f"下载 {runtime['label']}",
                            "url": runtime["help_url"],
                        }
                    )
            else:
                package = _package_for(item)
                launcher = Path(str(item.get("command") or "")).name.lower()
                if launcher.endswith(".exe"):
                    launcher = launcher[:-4]
                if package and launcher == "npx" and command_available("npx"):
                    commands.append(
                        _command(
                            "npm cache add",
                            ["npm", "cache", "add", package],
                            f"预下载 npm 包 {package}；第一次 npx 启动会更快。",
                        )
                    )
                if package and launcher == "uvx" and command_available("uv"):
                    commands.append(
                        _command(
                            "uv tool install",
                            ["uv", "tool", "install", package],
                            f"把 {package} 安装到 uv 的工具目录；卸载用 uv tool uninstall。",
                        )
                    )
            if item.get("id") == "mcp-playwright" and command_available("npx"):
                commands.append(
                    _command(
                        "playwright install",
                        ["npx", "-y", "playwright", "install", "chromium"],
                        "下载 Playwright 的 Chromium 浏览器（约 150 MB）。",
                    )
                )
            if item.get("id") == "mcp-blender":
                links.append(
                    {
                        "label": "blender-mcp 说明",
                        "url": "https://github.com/ahujasid/blender-mcp",
                    }
                )
        missing_env = [
            name
            for name in connector_env_names(item)
            if env_store is None or not env_store.is_configured(name)
        ]
        if missing_env:
            notes.append("还需要填写环境变量：" + "、".join(missing_env))
    else:
        declared = _skill_requirements(item)
        for raw in declared["downloads"]:
            url = str(raw.get("url") or "").strip()
            target = str(raw.get("path") or "").strip()
            if not url or not target:
                continue
            try:
                relative = safe_relative(target)
            except ResourceError:
                blocked.append(f"下载目标路径不合法：{target}")
                continue
            downloads.append(
                {
                    "url": url,
                    "path": relative.as_posix(),
                    "label": str(raw.get("label") or relative.as_posix()),
                }
            )
        missing_env = [
            name for name in declared["env"] if env_store is None or not env_store.is_configured(name)
        ]
        if missing_env:
            notes.append("还需要填写环境变量：" + "、".join(missing_env))
        missing_commands = [
            name for name in declared["commands"] if not command_available(name)
        ]
        if missing_commands:
            notes.append("还缺少命令：" + "、".join(missing_commands))

    return {
        "kind": kind,
        "id": str(item.get("id") or ""),
        "name": str(item.get("name") or item.get("id") or ""),
        "commands": commands,
        "downloads": downloads,
        "links": links,
        "notes": notes,
        "blocked": blocked,
        "has_actions": bool(commands or downloads),
    }


def run_command(argv: list[str], *, cwd: Path, timeout: int = 600) -> dict[str, Any]:
    executable = str(argv[0]) if argv else ""
    resolved = resolve_command(executable)
    if not resolved:
        return {"ok": False, "code": -1, "output": f"找不到命令：{executable}"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
    env = command_env()
    try:
        completed = subprocess.run(
            [resolved, *[str(part) for part in argv[1:]]],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, min(1800, int(timeout))),
            env=env,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": -1, "output": "命令超时，已停止。"}
    except OSError as exc:
        return {"ok": False, "code": -1, "output": f"启动失败：{exc}"}
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    if len(output) > MAX_PREPARE_OUTPUT:
        output = output[:MAX_PREPARE_OUTPUT] + "\n…（输出已截断）"
    return {
        "ok": completed.returncode == 0,
        "code": int(completed.returncode),
        "output": output or "（没有输出）",
    }


def download_skill_resources(
    skills: Any,
    skill_id: str,
    downloads: Iterable[dict[str, Any]],
    *,
    allow_private: bool = False,
    timeout: int = 45,
) -> list[dict[str, Any]]:
    """把技能声明的远程资源下载到技能目录；逐项返回结果。"""
    results: list[dict[str, Any]] = []
    total = 0
    for raw in downloads:
        url = str(raw.get("url") or "").strip()
        target = str(raw.get("path") or "").strip()
        label = str(raw.get("label") or target or url)
        try:
            relative = safe_relative(target)
            folder = skills.folder_for(skill_id)
            destination = (folder / relative).resolve()
            if not inside(folder, destination):
                raise ResourceError("下载路径越界。")
            blob = download_bytes(
                url,
                max_bytes=MAX_DOWNLOAD_BYTES,
                timeout=timeout,
                allow_private=allow_private,
            )
            total += len(blob.data)
            if total > MAX_DOWNLOAD_TOTAL:
                raise ResourceError(f"本次下载总量超过 {MAX_DOWNLOAD_TOTAL} B。")
            skills.write_attachment(skill_id, relative.as_posix(), blob.data)
            results.append(
                {
                    "label": label,
                    "path": relative.as_posix(),
                    "ok": True,
                    "bytes": len(blob.data),
                    "message": "已下载",
                }
            )
        except (ResourceError, NetworkError, OSError) as exc:
            results.append({"label": label, "path": target, "ok": False, "message": str(exc)})
    return results
