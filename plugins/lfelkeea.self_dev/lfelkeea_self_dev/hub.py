from __future__ import annotations

"""在线发现技能和 MCP 连接器。

技能生态和 MCP 生态都在 GitHub 上，这里只做三件事：找仓库、列出仓库里的
技能、把选中的技能/MCP 包拉回本地。所有下载都有大小和数量上限，二进制文件
直接跳过；安装动作仍由 ToolBox 走审批。
"""

import json
import posixpath
import re
import shutil
import urllib.parse
from typing import Any

from .net import NetworkError, http_request
from .resource_util import parse_front_matter

GITHUB_API = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"
GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

MAX_REPO_TEXT = 256 * 1024
MAX_SKILL_FILE = 64 * 1024
MAX_SKILL_FILES = 16
MAX_SKILL_CHARS = 40_000
MAX_SKILL_TOTAL = 195_000
MAX_SKILLS_PER_REPO = 30
SKIP_DIRS = {"__pycache__", ".git", ".github", "node_modules", ".venv", "venv"}
SKIP_SUFFIXES = {
    ".pyc", ".pyo", ".pyd", ".dll", ".exe", ".so", ".dylib", ".zip", ".7z",
    ".tar", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".mp3", ".mp4", ".wav", ".onnx", ".bin", ".pdf", ".woff", ".woff2",
    ".ttf", ".otf", ".db", ".sqlite", ".lock",
}
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _repo_parts(repo: str) -> tuple[str, str]:
    value = str(repo or "").strip().strip("/")
    if not REPO_RE.fullmatch(value):
        raise NetworkError("repo 必须是 owner/name 形式。")
    owner, name = value.split("/", 1)
    return owner, name


def _github_json(path: str, params: dict[str, Any] | None = None) -> Any:
    url = GITHUB_API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    response = http_request(url, timeout=25, headers=GITHUB_HEADERS)
    try:
        data = json.loads(response.text or "{}")
    except json.JSONDecodeError as exc:
        raise NetworkError(f"GitHub 返回的不是 JSON：{exc}") from exc
    if not response.ok:
        message = str(data.get("message") or f"HTTP {response.status}")
        if response.status == 403 and "rate limit" in message.lower():
            message = "GitHub 接口限流了，等一会儿再试，或先缩小搜索范围。"
        raise NetworkError(f"GitHub 请求失败：{message}")
    return data


def _default_ref(repo: str, ref: str = "") -> str:
    value = str(ref or "").strip()
    if value:
        return value
    owner, name = _repo_parts(repo)
    data = _github_json(f"/repos/{owner}/{name}")
    return str(data.get("default_branch") or "main")


def _tree(repo: str, ref: str = "") -> tuple[list[dict[str, Any]], str]:
    owner, name = _repo_parts(repo)
    branch = _default_ref(repo, ref)
    data = _github_json(f"/repos/{owner}/{name}/git/trees/{urllib.parse.quote(branch)}", {"recursive": "1"})
    tree = data.get("tree") if isinstance(data, dict) else None
    if not isinstance(tree, list):
        raise NetworkError(f"{repo} 里没有可读的文件树。")
    return [item for item in tree if isinstance(item, dict)], branch


def _raw_text(repo: str, ref: str, path: str, limit: int = MAX_REPO_TEXT) -> str:
    owner, name = _repo_parts(repo)
    safe_path = posixpath.normpath(str(path or "").replace("\\", "/")).lstrip("/")
    if safe_path.startswith("..") or "/../" in safe_path:
        raise NetworkError("文件路径越界。")
    url = f"{RAW_BASE}/{owner}/{name}/{urllib.parse.quote(ref, safe='')}/{urllib.parse.quote(safe_path)}"
    response = http_request(url, timeout=25, max_bytes=limit + 1)
    if not response.ok:
        raise NetworkError(f"读取 {safe_path} 失败：HTTP {response.status}")
    if len(response.text.encode("utf-8")) > limit:
        raise NetworkError(f"{safe_path} 超过 {limit} B，已跳过。")
    return response.text


def _repo_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo": str(item.get("full_name") or ""),
        "url": str(item.get("html_url") or ""),
        "description": str(item.get("description") or ""),
        "stars": int(item.get("stargazers_count") or 0),
        "updated": str(item.get("updated_at") or "")[:10],
        "language": str(item.get("language") or ""),
    }


def _search_repos(query: str, limit: int, extra: str = "") -> list[dict[str, Any]]:
    text = " ".join(str(query or "").split()).strip()
    words = " ".join(part for part in (text, extra) if part).strip()
    data = _github_json(
        "/search/repositories",
        {"q": words, "sort": "stars", "order": "desc", "per_page": max(1, min(20, limit))},
    )
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    return [_repo_summary(item) for item in items if isinstance(item, dict)]


def search_skill_repos(query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    text = " ".join(str(query or "").split()).strip() or "agent skills"
    extra = "" if "skill" in text.lower() else "skills"
    results = _search_repos(text, limit, extra)
    if not results:
        results = _search_repos(text, limit, "SKILL.md")
    return results[: max(1, min(20, int(limit or 8)))]


def search_mcp_repos(query: str = "", limit: int = 8) -> list[dict[str, Any]]:
    text = " ".join(str(query or "").split()).strip() or "mcp server"
    extra = "" if "mcp" in text.lower() else "mcp server"
    results = _search_repos(text, limit, extra)
    return results[: max(1, min(20, int(limit or 8)))]


def list_repo_skills(repo: str, ref: str = "") -> tuple[list[dict[str, str]], str]:
    tree, branch = _tree(repo, ref)
    found: list[dict[str, str]] = []
    for item in tree:
        path = str(item.get("path") or "")
        if str(item.get("type") or "") != "blob":
            continue
        if posixpath.basename(path).lower() != "skill.md":
            continue
        folder = posixpath.dirname(path)
        if any(part in SKIP_DIRS for part in folder.split("/")):
            continue
        found.append({"path": path, "folder": folder or "."})
        if len(found) >= MAX_SKILLS_PER_REPO:
            break
    return found, branch


def read_repo_text(repo: str, path: str, ref: str = "") -> str:
    branch = _default_ref(repo, ref)
    return _raw_text(repo, branch, path)


def fetch_skill(repo: str, path: str, ref: str = "") -> dict[str, Any]:
    tree, branch = _tree(repo, ref)
    clean = posixpath.normpath(str(path or "").replace("\\", "/")).lstrip("/")
    if posixpath.basename(clean).lower() != "skill.md":
        raise NetworkError("path 必须指向一个 SKILL.md。")
    if clean.startswith("..") or "/../" in clean:
        raise NetworkError("技能路径越界。")
    folder = posixpath.dirname(clean)
    raw_manifest = _raw_text(repo, branch, clean, MAX_SKILL_FILE * 2)
    metadata, body = parse_front_matter(raw_manifest)
    if len(body) < 10:
        raise NetworkError("这个 SKILL.md 正文太短，可能不是有效技能。")
    if len(body) > MAX_SKILL_CHARS:
        raise NetworkError(
            f"这个技能正文 {len(body)} 字，超过本地 {MAX_SKILL_CHARS} 字上限，未安装。"
            "可以换更精简的技能，或先用 read_repo_file 读正文，再拆成多个技能保存。"
        )

    resources: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    prefix = (folder + "/") if folder else ""
    candidates = []
    for item in tree:
        item_path = str(item.get("path") or "")
        if str(item.get("type") or "") != "blob" or item_path == clean:
            continue
        if prefix and not item_path.startswith(prefix):
            continue
        relative = item_path[len(prefix):] if prefix else item_path
        if not relative or any(part in SKIP_DIRS for part in relative.split("/")):
            continue
        # 根目录技能只带自己的 references/scripts/assets，避免把整个仓库拖下来。
        if not prefix and "/" in relative and relative.split("/", 1)[0] not in (
            "references",
            "scripts",
            "assets",
        ):
            continue
        # 同仓库的其它技能不应该变成这个技能的附件。
        if posixpath.basename(relative).lower() == "skill.md":
            continue
        if posixpath.splitext(relative)[1].lower() in SKIP_SUFFIXES:
            skipped.append(relative)
            continue
        if int(item.get("size") or 0) > MAX_SKILL_FILE:
            skipped.append(relative)
            continue
        candidates.append((relative, item_path))
        if len(candidates) >= MAX_SKILL_FILES:
            break

    for relative, item_path in candidates:
        try:
            text = _raw_text(repo, branch, item_path, MAX_SKILL_FILE)
        except NetworkError:
            skipped.append(relative)
            continue
        if "\x00" in text:
            skipped.append(relative)
            continue
        total += len(text.encode("utf-8"))
        if total > MAX_SKILL_TOTAL:
            skipped.append(relative)
            break
        resources[relative] = text

    return {
        "repo": str(repo).strip(),
        "ref": branch,
        "path": clean,
        "folder": folder,
        "name": str(metadata.get("name") or "").strip(),
        "description": str(metadata.get("description") or "").strip(),
        "body": body,
        "resources": resources,
        "skipped": skipped,
    }


def _npm_exists(package: str) -> bool:
    try:
        response = http_request(
            "https://registry.npmjs.org/" + urllib.parse.quote(package, safe="@"),
            timeout=20,
        )
    except NetworkError:
        return False
    return response.status == 200


def _pypi_exists(package: str) -> bool:
    try:
        response = http_request(
            "https://pypi.org/pypi/" + urllib.parse.quote(package) + "/json",
            timeout=20,
        )
    except NetworkError:
        return False
    return response.status == 200


def resolve_mcp_command(
    package: str,
    command: str = "",
    args: Any = None,
) -> dict[str, Any]:
    """根据包名推断启动命令，返回 command/args/source。

    调用方仍要检查可执行文件是否存在，并让主人确认。
    """
    name = str(package or "").strip()
    if not name:
        raise NetworkError("package 不能为空。")
    explicit = str(command or "").strip()
    argv = [str(item) for item in (args or [])] if isinstance(args, list) else []
    if explicit:
        return {"command": explicit, "args": argv, "source": "custom"}

    source = ""
    if name.startswith("npm:"):
        source, name = "npm", name[4:].strip()
    elif name.startswith("pypi:"):
        source, name = "pypi", name[5:].strip()
    if source == "npm":
        return {"command": "npx", "args": ["-y", name, *argv], "source": source}
    if source == "pypi":
        return {"command": "uvx", "args": [name, *argv], "source": source}

    if name.startswith("@") or "/" in name or _npm_exists(name):
        return {"command": "npx", "args": ["-y", name, *argv], "source": "npm"}
    if _pypi_exists(name):
        return {"command": "uvx", "args": [name, *argv], "source": "pypi"}
    raise NetworkError(f"npm 和 PyPI 上都没有找到「{name}」。需要时请显式传 command/args。")


def runtime_available(command: str) -> bool:
    if command in ("python", "python3"):
        return True
    return bool(shutil.which(command))
