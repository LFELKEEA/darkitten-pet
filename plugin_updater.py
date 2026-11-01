from __future__ import annotations

"""插件自动更新：读取插件库清单、下载 ZIP、校验并替换插件目录。"""

import hashlib
import json
import re
import shutil
import threading
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from updater import format_size, is_newer, parse_version, sha256_file

REGISTRY_URL = "https://lfelkeea.github.io/darkitten-launcher/plugins.json"
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
MAX_PLUGIN_BYTES = 64 * 1024 * 1024
MAX_EXTRACT_BYTES = 200 * 1024 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")


class PluginUpdateError(Exception):
    pass


@dataclass
class PluginRelease:
    id: str
    name: str
    version: str
    download_url: str
    sha256: str = ""
    size: int = 0
    description: str = ""
    page_url: str = ""
    notes: list[str] = field(default_factory=list)
    author: str = ""
    permissions: list[str] = field(default_factory=list)
    requires: list[dict[str, str]] = field(default_factory=list)
    featured: bool = False
    bundled: bool = False

    @property
    def size_text(self) -> str:
        return format_size(self.size)


def fetch_registry(url: str = REGISTRY_URL, timeout: int = 20) -> dict:
    text = str(url or "").strip() or REGISTRY_URL
    scheme = urllib.parse.urlparse(text).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise PluginUpdateError("插件库地址必须以 http://、https:// 或 file:// 开头")
    request = urllib.request.Request(
        text, headers={"User-Agent": "LFELKEEA-PluginUpdater/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_REGISTRY_BYTES + 1)
    except Exception as exc:
        raise PluginUpdateError(f"无法连接插件库：{exc}") from exc
    if len(raw) > MAX_REGISTRY_BYTES:
        raise PluginUpdateError("插件库清单过大")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise PluginUpdateError(f"插件库清单不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PluginUpdateError("插件库清单格式不正确")
    return data


def releases_from_registry(
    data: dict, base_url: str = REGISTRY_URL
) -> dict[str, PluginRelease]:
    entries = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise PluginUpdateError("插件库清单缺少 plugins 数组")
    releases: dict[str, PluginRelease] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        plugin_id = str(item.get("id", "")).strip()
        version = str(item.get("version", "")).strip().lstrip("vV")
        file = str(item.get("file") or item.get("download_url") or "").strip()
        if not plugin_id or not version or not file:
            continue
        if not _ID_RE.match(plugin_id):
            continue
        url = urllib.parse.urljoin(str(base_url or REGISTRY_URL), file)
        digest = str(item.get("sha256", "") or "").strip().lower()
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            digest = ""
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        permissions = [
            str(value).strip()
            for value in item.get("permissions", []) or []
            if str(value).strip()
        ]
        requires: list[dict[str, str]] = []
        for entry in item.get("requires", []) or []:
            if not isinstance(entry, dict):
                continue
            requires.append(
                {
                    "name": str(entry.get("name", "")).strip(),
                    "version": str(entry.get("version", "")).strip(),
                    "url": str(entry.get("url", "")).strip(),
                    "note": str(entry.get("note", "")).strip(),
                }
            )
        releases[plugin_id] = PluginRelease(
            id=plugin_id,
            name=str(item.get("name", plugin_id)).strip() or plugin_id,
            version=version,
            download_url=url,
            sha256=digest,
            size=max(0, size),
            description=str(item.get("description", "")).strip(),
            page_url=urllib.parse.urljoin(str(base_url or REGISTRY_URL), str(item.get("page_url", "") or "")),
            author=str(item.get("author", "")).strip(),
            permissions=permissions,
            requires=requires,
            featured=bool(item.get("featured", False)),
            bundled=bool(item.get("bundled", False)),
        )
    return releases


def check_updates(
    installed: dict[str, str],
    url: str = REGISTRY_URL,
    timeout: int = 20,
) -> dict[str, PluginRelease]:
    data = fetch_registry(url, timeout=timeout)
    releases = releases_from_registry(data, url)
    found: dict[str, PluginRelease] = {}
    for plugin_id, version in installed.items():
        release = releases.get(plugin_id)
        if release is None:
            continue
        try:
            if is_newer(release.version, version):
                found[plugin_id] = release
        except Exception:
            continue
    return found


def download_plugin(
    release: PluginRelease,
    dest_dir: Path,
    *,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if not release.download_url:
        raise PluginUpdateError("插件库缺少下载地址")
    if not release.sha256:
        raise PluginUpdateError("插件库缺少 SHA-256，已停止更新以保护安全")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{release.id}-{release.version}.zip"
    progress = progress_cb or (lambda _p, _m: None)
    cancel_event = cancel_event or threading.Event()

    if dest.exists():
        try:
            if not release.size or dest.stat().st_size == release.size:
                if sha256_file(dest, cancel_event).lower() == release.sha256:
                    progress(100, "插件包已下载")
                    return dest
        except Exception:
            pass
        _remove_path(dest)

    progress(1, f"正在下载 {release.name} {release.version}…")
    request = urllib.request.Request(
        release.download_url, headers={"User-Agent": "LFELKEEA-PluginUpdater/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            done = 0
            with dest.open("wb") as handle:
                while True:
                    if cancel_event.is_set():
                        raise PluginUpdateError("已取消")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_PLUGIN_BYTES:
                        raise PluginUpdateError("插件包体积异常，已停止")
                    handle.write(chunk)
                    if total:
                        percent = max(1, min(99, int(done * 100 / total)))
                        progress(
                            percent,
                            f"{release.name} {done / 1024:.0f}/{total / 1024:.0f} KB",
                        )
                    else:
                        progress(50, f"{release.name} 已下载 {done / 1024:.0f} KB")
    except PluginUpdateError:
        _remove_path(dest)
        raise
    except Exception as exc:
        _remove_path(dest)
        raise PluginUpdateError(f"下载插件包失败：{exc}") from exc

    if release.size and dest.stat().st_size != release.size:
        _remove_path(dest)
        raise PluginUpdateError(
            f"插件包大小不对（期望 {release.size}，实际 {dest.stat().st_size}）"
        )
    if sha256_file(dest, cancel_event).lower() != release.sha256.lower():
        _remove_path(dest)
        raise PluginUpdateError("插件包 SHA-256 校验失败，已删除损坏文件")
    progress(100, "插件包下载完成")
    return dest


def _safe_extract(archive: Path, target: Path) -> None:
    target = Path(target)
    try:
        with zipfile.ZipFile(archive) as zf:
            total = sum(int(info.file_size) for info in zf.infolist())
            if total > MAX_EXTRACT_BYTES:
                raise PluginUpdateError("插件包解压后体积异常，已停止")
            root = target.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise PluginUpdateError("插件包包含不安全的路径")
                destination = (target / pure).resolve()
                if destination != root and root not in destination.parents:
                    raise PluginUpdateError("插件包包含不安全的路径")
            zf.extractall(target)
    except PluginUpdateError:
        raise
    except zipfile.BadZipFile as exc:
        raise PluginUpdateError("插件包不是有效的 ZIP 文件") from exc
    except Exception as exc:
        raise PluginUpdateError(f"解压插件包失败：{exc}") from exc


def _payload_root(staging: Path) -> Path:
    entries = list(Path(staging).iterdir())
    dirs = [item for item in entries if item.is_dir()]
    if len(dirs) == 1 and not any(item.is_file() for item in entries):
        return dirs[0]
    return Path(staging)


def _read_manifest(folder: Path) -> dict:
    path = Path(folder) / "plugin.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PluginUpdateError("插件包缺少 plugin.json") from exc
    except ValueError as exc:
        raise PluginUpdateError(f"插件包的 plugin.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PluginUpdateError("插件包的 plugin.json 格式不正确")
    return data


def install_plugin(
    archive: Path,
    plugins_dir: Path,
    expected_id: str,
    expected_version: str = "",
    *,
    staging_dir: Path | None = None,
    backup_root: Path | None = None,
) -> Path:
    """解压并替换 data/plugins/<id>；已有的 .state 存储不受影响。"""
    archive = Path(archive)
    plugins_dir = Path(plugins_dir)
    if not archive.is_file():
        raise PluginUpdateError("插件包不存在")
    plugins_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(staging_dir) if staging_dir else plugins_dir.parent / "updates" / "plugin-staging"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    staging = staging_root / f"{expected_id}-{stamp}"
    _remove_path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _safe_extract(archive, staging)
        payload = _payload_root(staging)
        manifest = _read_manifest(payload)
        plugin_id = str(manifest.get("id", "")).strip()
        if plugin_id != expected_id:
            raise PluginUpdateError(
                f"插件包 id 不匹配（期望 {expected_id}，实际 {plugin_id or '空'}）"
            )
        packed = str(manifest.get("version", "")).strip().lstrip("vV")
        if expected_version and packed:
            try:
                if parse_version(packed) != parse_version(expected_version):
                    raise PluginUpdateError(
                        f"插件包版本不匹配（期望 {expected_version}，实际 {packed}）"
                    )
            except PluginUpdateError:
                raise
            except Exception as exc:
                raise PluginUpdateError(f"插件包版本号无效：{exc}") from exc
        entry = str(manifest.get("entry", "main.py")).strip() or "main.py"
        if not (payload / entry).is_file():
            raise PluginUpdateError(f"插件包缺少入口文件：{entry}")

        target = plugins_dir / expected_id
        backup_dir = Path(backup_root) if backup_root else plugins_dir / ".state" / "backups"
        backup = backup_dir / f"{expected_id}-{stamp}"
        if target.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            _remove_path(backup)
            try:
                shutil.copytree(target, backup)
            except Exception as exc:
                raise PluginUpdateError(f"备份旧插件失败：{exc}") from exc
            try:
                shutil.rmtree(target)
            except Exception as exc:
                raise PluginUpdateError(f"删除旧插件失败：{exc}") from exc
        try:
            shutil.move(str(payload), str(target))
        except Exception as exc:
            if backup.exists() and not target.exists():
                try:
                    shutil.copytree(backup, target)
                except Exception:
                    pass
            raise PluginUpdateError(f"安装新插件失败：{exc}") from exc
        return target
    finally:
        _remove_path(staging)
        try:
            if staging_root.is_dir() and not any(staging_root.iterdir()):
                staging_root.rmdir()
        except OSError:
            pass


def _remove_path(path: Path) -> None:
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
    except OSError:
        pass
