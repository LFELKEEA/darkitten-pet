from __future__ import annotations

"""GPT-SoVITS 进程管理：插件启用时在后台启动，插件或桌宠退出时关闭。

只管理由本插件启动的实例；用户自己开着的服务不会被结束。
启动方式和 SnowLuma 一样：隐藏控制台窗口，子进程挂在作业对象上，
桌宠异常退出时由系统兜底结束，避免留下占显存的孤儿进程。
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .snowluma import (  # noqa: WPS436 - 复用同一套 Windows 进程工具
    CREATE_NEW_PROCESS_GROUP,
    CREATE_NO_WINDOW,
    IS_WINDOWS,
    SW_HIDE,
    _WindowsJob,
    port_open,
    url_endpoint,
)

# 整合包里的固定入口，两份文件都在才算有效的安装目录。
API_SCRIPT = "api_v2.py"
RUNTIME_PYTHON = Path("runtime") / "python.exe"
TTS_CONFIG = Path("GPT_SoVITS") / "configs" / "tts_infer.yaml"
DEFAULT_URL = "http://127.0.0.1:9880"

# 自动探测时优先看的位置，顺序即优先级。
_COMMON_ROOTS = (
    "D:/GPT-SoVITS",
    "C:/GPT-SoVITS",
    "E:/GPT-SoVITS",
)


def _clean_path(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def is_sovits_home(folder: Path) -> bool:
    """判断目录是不是可以直接启动的整合包根目录。"""
    try:
        return (folder / API_SCRIPT).is_file() and (
            folder / RUNTIME_PYTHON
        ).is_file()
    except OSError:
        return False


def _resolve_home(folder: Path) -> Path | None:
    """accept 传入目录本身、它的 app 子目录，或它下面的 GPT-SoVITS* 子目录。"""
    try:
        if is_sovits_home(folder):
            return folder.resolve()
        app = folder / "app"
        if is_sovits_home(app):
            return app.resolve()
        for child in folder.iterdir():
            if child.is_dir() and child.name.lower().startswith("gpt-sovits"):
                if is_sovits_home(child):
                    return child.resolve()
    except OSError:
        return None
    return None


def detect_sovits_dir(configured: str = "") -> Path | None:
    """优先用设置里的目录，其次在常见位置找整合包；找不到返回 None。"""
    text = _clean_path(configured)
    if text:
        # 用户显式填了路径就以它为准，找不到时不要悄悄换成别的副本。
        return _resolve_home(Path(text))

    candidates: list[Path] = [Path(item) for item in _COMMON_ROOTS]
    candidates.extend(
        [
            Path.home() / "GPT-SoVITS",
            Path.home() / "Desktop" / "GPT-SoVITS",
            Path.home() / "Downloads" / "GPT-SoVITS",
        ]
    )
    for candidate in candidates:
        found = _resolve_home(candidate)
        if found is not None:
            return found
    return None


def ref_voice_files(home: Path) -> list[dict[str, str]]:
    """列出参考音色：整合包自己的 refs 目录和上一层目录的 refs。

    同名的 .txt 会被当成参考文字一起返回，方便界面一键切换音色。
    """
    folders: list[Path] = []
    for folder in (Path(home) / "refs", Path(home).parent / "refs"):
        if folder.is_dir() and folder not in folders:
            folders.append(folder)
    voices: list[dict[str, str]] = []
    for folder in folders:
        try:
            files = sorted(
                (item for item in folder.glob("*.wav") if item.is_file()),
                key=lambda item: item.name.lower(),
            )
        except OSError:
            continue
        for item in files:
            text = ""
            note = item.with_suffix(".txt")
            try:
                if note.is_file():
                    text = note.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            voices.append({"name": item.stem, "path": str(item), "text": text})
    voices.sort(key=lambda voice: voice["name"].lower())
    return voices


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


class SovitsProcess:
    """一个隐藏窗口运行的 GPT-SoVITS API 实例。"""

    _MAX_LOG_BYTES = 8 * 1024 * 1024

    def __init__(self, folder: Path, python: Path, log_path: Path, host: str, port: int) -> None:
        self.folder = Path(folder)
        self.python = Path(python)
        self.log_path = Path(log_path)
        self.host = host
        self.port = int(port)
        self._proc: subprocess.Popen[bytes] | None = None
        self._job = _WindowsJob()
        self._lock = threading.Lock()

    @property
    def pid(self) -> int:
        proc = self._proc
        return int(proc.pid) if proc is not None else 0

    def is_alive(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    def exit_code(self) -> int | None:
        proc = self._proc
        return proc.poll() if proc is not None else None

    def _rotate_log(self) -> None:
        try:
            if self.log_path.exists() and self.log_path.stat().st_size > self._MAX_LOG_BYTES:
                backup = self.log_path.with_suffix(".1.log")
                if backup.exists():
                    backup.unlink()
                self.log_path.replace(backup)
        except OSError:
            pass

    def _command(self) -> list[str]:
        return [
            str(self.python),
            API_SCRIPT,
            "-a",
            self.host,
            "-p",
            str(self.port),
            "-c",
            str(TTS_CONFIG),
        ]

    def start(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_log()
        stream = open(self.log_path, "a", encoding="utf-8", errors="replace", buffering=1)
        try:
            stream.write(
                f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 由 QQ 桥接插件启动 =====\n"
            )
            stream.flush()
            kwargs: dict[str, Any] = {}
            if IS_WINDOWS:
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = SW_HIDE
                kwargs["startupinfo"] = startup
                kwargs["creationflags"] = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True
            job_ready = self._job.open()
            try:
                proc = subprocess.Popen(
                    self._command(),
                    cwd=str(self.folder),
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    **kwargs,
                )
            except Exception:
                if job_ready:
                    self._job.close()
                raise
        finally:
            stream.close()
        self._proc = proc
        if job_ready:
            self._job.assign(proc.pid)
        return int(proc.pid)

    def stop(self, timeout: float = 6.0) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._job.close()


class SovitsManager:
    """按设置自动启动/停止 GPT-SoVITS；只结束自己启动的实例。"""

    def __init__(
        self,
        state_root: Path,
        log: Callable[[str], None],
        on_ready: Callable[[], None] | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self._log = log
        self._on_ready = on_ready
        self._lock = threading.RLock()
        self._auto = False
        self._engine = "windows"
        self._folder_setting = ""
        self._url = DEFAULT_URL
        # 首次加载模型要读几个大权重文件，慢盘上可能接近一分钟。
        self._timeout = 180.0
        self._process: SovitsProcess | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._stopping = True
        self._external = False
        self._error = ""
        self._folder_cache = ""

    # ---------- 配置 ----------

    def configure(
        self,
        settings: dict[str, Any],
        url: str | None = None,
        engine: str | None = None,
    ) -> None:
        with self._lock:
            self._auto = bool(settings.get("tts_auto_start"))
            self._folder_setting = str(settings.get("tts_sovits_dir") or "")
            self._url = str(url or settings.get("tts_gpt_sovits_url") or DEFAULT_URL)
            self._engine = str(engine or settings.get("tts_engine") or "windows")

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._engine == "gpt_sovits"

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            folder = self._folder_cache
            if process is not None and process.is_alive():
                return {
                    "managed": True,
                    "pid": process.pid,
                    "alive": True,
                    "external": False,
                    "dir": folder,
                    "url": self._url,
                    "error": "",
                }
            return {
                "managed": False,
                "pid": process.pid if process is not None else 0,
                "alive": False,
                "external": self._external,
                "dir": folder,
                "url": self._url,
                "error": self._error,
            }

    # ---------- 路径 ----------

    def home(self) -> Path | None:
        """探测到的整合包目录；找不到返回 None。"""
        with self._lock:
            setting = self._folder_setting
        return detect_sovits_dir(setting)

    def voices(self) -> list[dict[str, str]]:
        home = self.home()
        if home is None:
            return []
        return ref_voice_files(home)

    # ---------- 启动 ----------

    def start_async(self, force: bool = False) -> None:
        with self._lock:
            if self._engine != "gpt_sovits":
                return
            if not self._auto and not force:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            if self._process is not None and self._process.is_alive():
                return
            self._stopping = False
            self._error = ""
            generation = self._generation
            thread = threading.Thread(
                target=self.ensure_started,
                args=(generation, force),
                name="qq-bridge-sovits",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def ensure_started(self, generation: int | None = None, force: bool = False) -> None:
        """检查并启动；同步执行，线程和测试都可以直接调用。"""
        with self._lock:
            generation = self._generation if generation is None else generation
            if self._engine != "gpt_sovits":
                return
            if (not self._auto and not force) or self._stopping or generation != self._generation:
                return
            url = self._url
            folder_setting = self._folder_setting
            timeout = self._timeout

        host, port = url_endpoint(url, 9880)
        if port_open(host, port):
            self._set_external("GPT-SoVITS 已经在运行，插件不会接管这个实例。")
            return

        home = detect_sovits_dir(folder_setting)
        if home is None:
            self._fail(
                "没找到 GPT-SoVITS 目录：需要包含 api_v2.py 和 runtime\\python.exe，"
                "请在「语音」页点自动检测或手动选择。"
            )
            return

        with self._lock:
            self._folder_cache = str(home)
        process = SovitsProcess(
            home,
            home / RUNTIME_PYTHON,
            self.state_root / "sovits-stdout.log",
            host,
            port,
        )
        try:
            pid = process.start()
        except OSError as exc:
            self._fail(f"启动 GPT-SoVITS 失败：{exc}")
            return

        with self._lock:
            if self._stopping or generation != self._generation:
                process.stop()
                return
            self._process = process

        self._log(f"GPT-SoVITS 已在后台启动（PID {pid}），等待 TTS 服务就绪…")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._stopping or generation != self._generation:
                    return
            if not process.is_alive():
                self._fail(
                    f"GPT-SoVITS 启动后退出（退出码 {process.exit_code()}），"
                    f"日志：{process.log_path}"
                )
                process.stop()
                with self._lock:
                    if self._process is process:
                        self._process = None
                return
            if port_open(host, port):
                self._log(f"GPT-SoVITS TTS 已就绪：http://{host}:{port}")
                callback = self._on_ready
                if callback is not None:
                    try:
                        callback()
                    except Exception as exc:
                        self._log(f"GPT-SoVITS 就绪回调失败：{exc}")
                return
            time.sleep(1.0)

        self._fail(
            f"GPT-SoVITS 已启动，但 {port} 端口在 {int(timeout)} 秒内没就绪，"
            f"日志：{process.log_path}"
        )

    def ensure_ready(self, timeout: float | None = None, force: bool = False) -> bool:
        """合成前的检查：没启动就拉起来并等到端口可用。可在工作线程调用。"""
        with self._lock:
            if self._engine != "gpt_sovits" and not force:
                return False
            url = self._url
            auto = self._auto
        host, port = url_endpoint(url, 9880)
        if port_open(host, port):
            return True
        if not auto and not force:
            self._log(
                "GPT-SoVITS 没有运行，自动启动已关闭；在「语音」页点启动，"
                "或打开 D:\\GPT-SoVITS\\启动TTS服务.bat。"
            )
            return False
        self.start_async(force=True)
        deadline = time.monotonic() + float(timeout or self._timeout)
        while time.monotonic() < deadline:
            if port_open(host, port):
                return True
            with self._lock:
                process = self._process
            if process is not None and not process.is_alive():
                return False
            time.sleep(1.0)
        return False

    def _set_external(self, message: str) -> None:
        with self._lock:
            self._external = True
            self._error = ""
        self._log(message)

    def _fail(self, message: str) -> None:
        with self._lock:
            self._error = message
        self._log(message)

    # ---------- 停止 ----------

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._generation += 1
            process = self._process
            self._process = None
            self._thread = None
        if process is None:
            return
        pid = process.pid
        try:
            process.stop()
        except Exception as exc:
            self._log(f"关闭 GPT-SoVITS 失败：{exc}")
            return
        self._log(f"已关闭由插件启动的 GPT-SoVITS（PID {pid}）。")
