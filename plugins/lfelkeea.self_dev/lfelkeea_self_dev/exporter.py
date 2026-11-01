from __future__ import annotations

"""把已安装的桌宠插件导出成 ZIP，方便备份、分享或投稿到插件市场。"""

import json
import re
import zipfile
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
EXCLUDE_DIRS = {"__pycache__", ".state", ".git", ".svn", ".hg"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


class ExportError(Exception):
    pass


def _manifest(folder: Path) -> dict[str, Any]:
    path = folder / "plugin.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def list_plugins(plugins_dir: Path | str) -> list[dict[str, Any]]:
    root = Path(plugins_dir).resolve()
    items: list[dict[str, Any]] = []
    if not root.is_dir():
        return items
    for folder in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not folder.is_dir() or not ID_RE.match(folder.name):
            continue
        if not (folder / "plugin.json").is_file():
            continue
        manifest = _manifest(folder)
        items.append(
            {
                "id": folder.name,
                "name": str(manifest.get("name") or folder.name),
                "version": str(manifest.get("version") or "0.0.0"),
                "description": str(manifest.get("description") or ""),
                "folder": str(folder),
            }
        )
    return items


def export_plugin(
    plugins_dir: Path | str,
    plugin_id: str,
    destination: Path | str,
) -> dict[str, Any]:
    pid = str(plugin_id or "").strip()
    if not ID_RE.match(pid):
        raise ExportError("插件 id 不合法。")
    root = Path(plugins_dir).resolve()
    folder = (root / pid).resolve()
    if root not in folder.parents or not folder.is_dir():
        raise ExportError(f"没有找到插件：{pid}")
    if not (folder / "plugin.json").is_file():
        raise ExportError(f"{pid} 缺少 plugin.json，无法导出。")

    target = Path(destination).expanduser()
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    if target.is_dir():
        raise ExportError("导出目标是一个目录，请指定 .zip 文件路径。")
    target.parent.mkdir(parents=True, exist_ok=True)

    files = 0
    total = 0
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in EXCLUDE_DIRS for part in path.relative_to(folder).parts[:-1]):
                    continue
                if path.suffix.lower() in EXCLUDE_SUFFIXES:
                    continue
                rel = path.relative_to(folder).as_posix()
                archive.write(path, f"{pid}/{rel}")
                files += 1
                total += path.stat().st_size
    except OSError as exc:
        raise ExportError(f"写入 ZIP 失败：{exc}") from exc
    except zipfile.BadZipFile as exc:
        raise ExportError(f"ZIP 写入异常：{exc}") from exc

    manifest = _manifest(folder)
    return {
        "plugin_id": pid,
        "name": str(manifest.get("name") or pid),
        "version": str(manifest.get("version") or "0.0.0"),
        "path": str(target.resolve()),
        "files": files,
        "bytes": total,
        "zip_bytes": target.stat().st_size,
    }
