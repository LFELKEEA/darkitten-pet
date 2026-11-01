from __future__ import annotations

"""桌宠自动更新。

更新包是 PyInstaller onedir 产物：根目录下是 LFELKEEA.exe 和 _internal/。
运行中的 exe 不能被覆盖，所以下载和校验完成后会启动一个隐藏的 PowerShell
脚本，等桌宠退出后替换程序文件，再重新启动。data/ 目录完全不动。
"""

import hashlib
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
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from version import APP_VERSION

UPDATE_URL = "https://lfelkeea.github.io/darkitten-launcher/update.json"
PET_FALLBACK_URL = "https://lfelkeea.github.io/darkitten-launcher/pet_release.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_UPDATE_BYTES = 500 * 1024 * 1024
MAX_EXTRACT_BYTES = 900 * 1024 * 1024
ProgressCallback = Callable[[int, str], None]


class UpdateError(Exception):
    pass


@dataclass
class PetRelease:
    version: str
    notes: list[str] = field(default_factory=list)
    download_url: str = ""
    page_url: str = ""
    sha256: str = ""
    size: int = 0
    released: str = ""
    manifest_url: str = ""

    @property
    def size_text(self) -> str:
        return format_size(self.size)


def format_size(size: int) -> str:
    value = max(0, int(size or 0))
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} B"


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", text)
    if not match:
        raise UpdateError(f"无法识别版本号：{value!r}")
    parts = [int(item) for item in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, current: str) -> bool:
    return parse_version(remote) > parse_version(current)


def install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def updates_dir(root: Path | None = None) -> Path:
    return Path(root or install_dir()) / "data" / "updates"


def _url_join(base: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return urllib.parse.urljoin(base, text)


def _notes(value: object) -> list[str]:
    if isinstance(value, str):
        raw = value.splitlines()
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return []
    result: list[str] = []
    for line in raw:
        text = str(line).strip().lstrip("-*•").strip()
        if text:
            result.append(text)
    return result[:40]


def release_from_manifest(data: dict, manifest_url: str = UPDATE_URL) -> PetRelease:
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确")
    payload = data.get("pet") if isinstance(data.get("pet"), dict) else data
    version = str(
        payload.get("version")
        or payload.get("latest_version")
        or data.get("latest_version")
        or ""
    ).strip()
    if not version:
        raise UpdateError("更新清单缺少桌宠版本号")
    download = _url_join(
        manifest_url,
        payload.get("download_url")
        or payload.get("url")
        or data.get("download_url")
        or "",
    )
    page = _url_join(
        manifest_url,
        payload.get("page_url")
        or payload.get("release_url")
        or data.get("page_url")
        or "",
    )
    digest = str(payload.get("sha256") or data.get("sha256") or "").strip().lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("更新清单里的 SHA-256 格式不正确")
    try:
        size = int(payload.get("size") or data.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return PetRelease(
        version=version.lstrip("vV"),
        notes=_notes(payload.get("notes") or data.get("notes")),
        download_url=download,
        page_url=page,
        sha256=digest,
        size=max(0, size),
        released=str(payload.get("released") or data.get("released") or ""),
        manifest_url=manifest_url,
    )


def fetch_manifest(url: str, timeout: int = 20) -> dict:
    text = str(url or "").strip()
    scheme = urllib.parse.urlparse(text).scheme.lower()
    if scheme not in ("http", "https", "file"):
        raise UpdateError("更新源地址必须以 http://、https:// 或 file:// 开头")
    request = urllib.request.Request(
        text, headers={"User-Agent": "LFELKEEA-Updater/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
    except Exception as exc:
        raise UpdateError(f"无法连接更新源：{exc}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("更新清单过大")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise UpdateError(f"更新清单不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确")
    return data


def check_for_update(
    current: str = APP_VERSION,
    url: str = UPDATE_URL,
    timeout: int = 20,
) -> PetRelease | None:
    manifest_url = str(url or "").strip() or UPDATE_URL
    data = fetch_manifest(manifest_url, timeout=timeout)
    if not isinstance(data.get("pet"), dict):
        # 兼容单独的 pet_release.json
        if not data.get("version"):
            try:
                data = fetch_manifest(PET_FALLBACK_URL, timeout=timeout)
                manifest_url = PET_FALLBACK_URL
            except UpdateError:
                return None
    release = release_from_manifest(data, manifest_url)
    if not is_newer(release.version, current):
        return None
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址")
    if not release.sha256:
        raise UpdateError("更新清单缺少 SHA-256，已停止更新以保护安全")
    return release


def sha256_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise UpdateError("已取消")
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
    release: PetRelease,
    dest_dir: Path,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址")
    if not release.sha256:
        raise UpdateError("更新清单缺少 SHA-256，已停止下载")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"LFELKEEA-{release.version}.zip"
    progress = progress_cb or (lambda _p, _m: None)
    cancel_event = cancel_event or threading.Event()

    if dest.exists():
        try:
            if dest.stat().st_size == release.size or not release.size:
                if sha256_file(dest, cancel_event).lower() == release.sha256:
                    progress(100, "更新包已下载")
                    return dest
        except UpdateError:
            raise
        except Exception:
            pass
        try:
            dest.unlink()
        except OSError:
            pass

    progress(1, f"正在下载 LFELKEEA {release.version}…")
    request = urllib.request.Request(
        release.download_url, headers={"User-Agent": "LFELKEEA-Updater/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            total = int(response.headers.get("Content-Length") or release.size or 0)
            done = 0
            with dest.open("wb") as handle:
                while True:
                    if cancel_event.is_set():
                        raise UpdateError("已取消")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    done += len(chunk)
                    if done > MAX_UPDATE_BYTES:
                        raise UpdateError("更新包体积异常，已停止")
                    handle.write(chunk)
                    if total:
                        percent = max(1, min(99, int(done * 100 / total)))
                        progress(
                            percent,
                            f"{done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
                        )
                    else:
                        progress(50, f"已下载 {done / 1024 / 1024:.1f} MB")
    except UpdateError:
        _remove_path(dest)
        raise
    except Exception as exc:
        _remove_path(dest)
        raise UpdateError(f"下载更新包失败：{exc}") from exc

    if release.size and dest.stat().st_size != release.size:
        _remove_path(dest)
        raise UpdateError(
            f"更新包大小不对（期望 {release.size}，实际 {dest.stat().st_size}）"
        )
    digest = sha256_file(dest, cancel_event)
    if digest.lower() != release.sha256.lower():
        _remove_path(dest)
        raise UpdateError("更新包 SHA-256 校验失败，已删除损坏文件")
    progress(100, "更新包下载完成")
    return dest


def _safe_extract(archive: Path, target: Path) -> None:
    target = Path(target)
    try:
        with zipfile.ZipFile(archive) as zf:
            total = sum(int(info.file_size) for info in zf.infolist())
            if total > MAX_EXTRACT_BYTES:
                raise UpdateError("更新包解压后体积异常，已停止")
            root = target.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise UpdateError("更新包包含不安全的路径")
                destination = (target / pure).resolve()
                if destination != root and root not in destination.parents:
                    raise UpdateError("更新包包含不安全的路径")
            zf.extractall(target)
    except UpdateError:
        raise
    except zipfile.BadZipFile as exc:
        raise UpdateError("更新包不是有效的 ZIP 文件") from exc
    except Exception as exc:
        raise UpdateError(f"解压更新包失败：{exc}") from exc


def _payload_root(staging: Path) -> Path:
    entries = [item for item in Path(staging).iterdir()]
    dirs = [item for item in entries if item.is_dir()]
    if len(dirs) == 1 and not any(item.is_file() for item in entries):
        return dirs[0]
    return Path(staging)


def _read_packed_version(root: Path) -> str:
    for path in (root / "version.json", root / "_internal" / "version.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            value = str(data.get("version", "") or "").strip()
            if value:
                return value
    return ""


def prepare_update(
    archive: Path, dest_dir: Path, expected_version: str = ""
) -> Path:
    archive = Path(archive)
    if not archive.is_file():
        raise UpdateError("更新包不存在")
    staging = Path(dest_dir) / f"staging-{expected_version or 'latest'}"
    _remove_path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, staging)
    payload = _payload_root(staging)
    exe = payload / "LFELKEEA.exe"
    if not exe.is_file():
        raise UpdateError("更新包缺少 LFELKEEA.exe")
    if not (payload / "_internal").is_dir():
        raise UpdateError("更新包缺少 _internal 目录")
    try:
        with exe.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise UpdateError("更新包里的 LFELKEEA.exe 不是有效的 Windows 程序")
    except OSError as exc:
        raise UpdateError(f"读取更新包失败：{exc}") from exc
    packed = _read_packed_version(payload)
    if expected_version and packed:
        try:
            if parse_version(packed) != parse_version(expected_version):
                raise UpdateError(
                    f"更新包版本不匹配（期望 {expected_version}，实际 {packed}）"
                )
        except UpdateError:
            raise
        except Exception as exc:
            raise UpdateError(f"更新包版本号无效：{exc}") from exc
    return payload


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


def apply_script_text() -> str:
    return r'''param(
  [Parameter(Mandatory=$true)][int]$PetPid,
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Install,
  [Parameter(Mandatory=$true)][int]$Restart,
  [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) {
  try {
    $dir = Split-Path -Parent $LogPath
    if (-not (Test-Path -LiteralPath $dir)) {
      New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding UTF8
  } catch {}
}

function Remove-Path([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Move-WithRetry([string]$From, [string]$To) {
  for ($i = 0; $i -lt 40; $i++) {
    try {
      Move-Item -LiteralPath $From -Destination $To -Force -ErrorAction Stop
      return $true
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  return $false
}

try {
  Write-Log "waiting for LFELKEEA pid $PetPid"
  $deadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $PetPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 400
  }
  Start-Sleep -Milliseconds 800

  $exe = Join-Path $Install 'LFELKEEA.exe'
  $internal = Join-Path $Install '_internal'
  $srcExe = Join-Path $Source 'LFELKEEA.exe'
  $srcInternal = Join-Path $Source '_internal'
  if (-not (Test-Path -LiteralPath $srcExe)) { throw 'new LFELKEEA.exe is missing' }
  if (-not (Test-Path -LiteralPath $srcInternal)) { throw 'new _internal is missing' }

  $newExe = "$exe.new"
  $newInternal = "$internal.new"
  Remove-Path $newExe
  Remove-Path $newInternal
  Copy-Item -LiteralPath $srcExe -Destination $newExe -Force
  Copy-Item -LiteralPath $srcInternal -Destination $newInternal -Recurse -Force

  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = Join-Path $Install ("data\updates\backup-" + $stamp)
  New-Item -ItemType Directory -Force -Path $backup | Out-Null
  Write-Log "backup: $backup"

  $oldExe = "$exe.old"
  $oldInternal = "$internal.old"
  Remove-Path $oldExe
  Remove-Path $oldInternal

  if (Test-Path -LiteralPath $exe) {
    if (-not (Move-WithRetry $exe $oldExe)) { throw 'cannot move old LFELKEEA.exe' }
  }
  if (Test-Path -LiteralPath $internal) {
    if (-not (Move-WithRetry $internal $oldInternal)) { throw 'cannot move old _internal' }
  }
  if (-not (Move-WithRetry $newExe $exe)) { throw 'cannot install new LFELKEEA.exe' }
  if (-not (Move-WithRetry $newInternal $internal)) { throw 'cannot install new _internal' }

  if (Test-Path -LiteralPath $oldExe) {
    Move-Item -LiteralPath $oldExe -Destination (Join-Path $backup 'LFELKEEA.exe') -Force -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $oldInternal) {
    Move-Item -LiteralPath $oldInternal -Destination (Join-Path $backup '_internal') -Force -ErrorAction SilentlyContinue
  }

  Write-Log 'update applied'
  Remove-Path $Source
  if ($Restart -eq 1) {
    Start-Process -FilePath $exe -WorkingDirectory $Install
    Write-Log 'restarted'
  }
  exit 0
} catch {
  Write-Log ("ERROR: " + $_.Exception.Message)
  try {
    $exe = Join-Path $Install 'LFELKEEA.exe'
    $internal = Join-Path $Install '_internal'
    $oldExe = "$exe.old"
    $oldInternal = "$internal.old"
    if ((Test-Path -LiteralPath $oldExe) -and -not (Test-Path -LiteralPath $exe)) {
      Move-Item -LiteralPath $oldExe -Destination $exe -Force
    }
    if ((Test-Path -LiteralPath $oldInternal) -and -not (Test-Path -LiteralPath $internal)) {
      Move-Item -LiteralPath $oldInternal -Destination $internal -Force
    }
    if ($Restart -eq 1 -and (Test-Path -LiteralPath $exe)) {
      Start-Process -FilePath $exe -WorkingDirectory $Install
    }
  } catch {}
  exit 1
}
'''


def write_apply_script(root: Path | None = None) -> Path:
    folder = updates_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "apply_pet_update.ps1"
    script.write_text(apply_script_text(), encoding="utf-8")
    return script


def apply_flags() -> int:
    """隐藏窗口启动 PowerShell 的创建标志。

    不能用 DETACHED_PROCESS：那种方式下 powershell.exe 没有控制台，
    进程会立刻退出，脚本一行都不会执行。CREATE_NO_WINDOW 既隐藏窗口
    又能正常执行；CREATE_NEW_PROCESS_GROUP 让脚本不受父进程控制台影响。
    """
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )


def _apply_command(
    script: Path,
    payload_root: Path,
    install: Path,
    log_path: Path,
    restart: bool,
    pid: int,
) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(script),
        "-PetPid",
        str(pid),
        "-Source",
        str(Path(payload_root).resolve()),
        "-Install",
        str(install.resolve()),
        "-Restart",
        "1" if restart else "0",
        "-LogPath",
        str(log_path.resolve()),
    ]


def read_apply_log(install: Path | None = None, limit: int = 400) -> str:
    """读更新脚本日志的最后一段，用于失败提示。"""
    path = updates_dir(install) / "apply.log"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " / ".join(lines[-3:])[-limit:]


def _wait_script_started(log_path: Path, timeout: float = 3.0) -> bool:
    deadline = timeout
    while deadline > 0:
        try:
            if log_path.is_file() and log_path.stat().st_size > 0:
                return True
        except OSError:
            pass
        time.sleep(0.15)
        deadline -= 0.15
    return False


_apply_process: subprocess.Popen | None = None


def schedule_apply(
    payload_root: Path,
    install: Path | None = None,
    *,
    restart: bool = True,
    pid: int | None = None,
) -> Path:
    if os.name != "nt":
        raise UpdateError("桌宠自动更新只支持 Windows")
    global _apply_process
    install = Path(install or install_dir())
    script = write_apply_script(install)
    log_path = updates_dir(install) / "apply.log"
    try:
        log_path.unlink()
    except OSError:
        pass
    command = _apply_command(
        script,
        payload_root,
        install,
        log_path,
        restart,
        os.getpid() if pid is None else int(pid),
    )
    # 先试隐藏控制台；万一环境不允许，再退回无创建标志（可能闪一下窗口）。
    attempts = [apply_flags(), 0]
    last_error = ""
    for flags in attempts:
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(install),
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            last_error = str(exc)
            continue
        _apply_process = proc
        if _wait_script_started(log_path):
            return script
        if proc.poll() is None:
            # 进程还活着，只是日志写得慢，不要重复启动第二个脚本。
            return script
        last_error = "更新脚本没有启动"
    detail = last_error or "未知原因"
    tail = read_apply_log(install)
    if tail:
        detail = f"{detail}（{tail}）"
    raise UpdateError(f"启动更新脚本失败：{detail}")
