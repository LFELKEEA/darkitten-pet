from __future__ import annotations

"""SnowLuma 进程管理：插件启用时后台启动，插件或桌宠退出时关闭。

只管理由本插件启动的实例；用户手动启动的 SnowLuma 不会被接管或结束。
"""

import csv
import ctypes
import io
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

IS_WINDOWS = os.name == "nt"

# Windows 进程创建标志：给子进程一个隐藏的控制台，并让它独立于桌宠的进程组。
# 这里不能用 DETACHED_PROCESS：SnowLuma 启动后还会自己再 spawn 一个 node 子进程，
# 父进程没有控制台时，Windows 会给这个子进程新建一个可见窗口
# （Win11 默认终端下就是截图里那个 Windows Terminal 标签页）。
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
SW_HIDE = 0

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def url_endpoint(url: str, default_port: int = 80) -> tuple[str, int]:
    """把 http://127.0.0.1:3003 拆成 (host, port)。"""
    parsed = urlparse(str(url or "").strip())
    host = parsed.hostname or "127.0.0.1"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parsed.scheme == "https" else default_port
    return host, int(port)


def port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def detect_snowluma_dir(configured: str = "") -> Path | None:
    """优先用设置里的目录，其次在常见位置查找包含 index.mjs 的目录。"""
    candidates: list[Path] = []
    text = str(configured or "").strip().strip('"').strip("'")
    if text:
        # 用户显式填了目录就以它为准，找不到时不要悄悄换成别的副本。
        candidate = Path(text)
        try:
            if (candidate / "index.mjs").is_file() and (
                (candidate / "node.exe").is_file() or shutil.which("node")
            ):
                return candidate.resolve()
        except OSError:
            pass
        return None
    candidates.extend(
        [
            Path("D:/SnowLuma"),
            Path("C:/SnowLuma"),
            Path.home() / "SnowLuma",
            Path.home() / "Desktop" / "SnowLuma",
            Path.home() / "Downloads" / "SnowLuma",
        ]
    )
    node_on_path = shutil.which("node")
    for candidate in candidates:
        try:
            if not (candidate / "index.mjs").is_file():
                continue
            if (candidate / "node.exe").is_file() or node_on_path:
                return candidate.resolve()
        except OSError:
            continue
    return None


def webui_endpoint(folder: Path) -> tuple[str, int]:
    runtime = _read_json(folder / "config" / "runtime.json")
    host = str(runtime.get("webuiHost") or "127.0.0.1")
    try:
        port = int(runtime.get("webuiPort") or 5099)
    except (TypeError, ValueError):
        port = 5099
    return host, port


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def pid_listening_on(port: int) -> int:
    """用 netstat 找出监听指定端口的进程号，找不到返回 0。"""
    if not port:
        return 0
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=8,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    suffix = f":{int(port)}"
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() != "LISTENING":
            continue
        local = parts[1]
        if not local.endswith(suffix):
            continue
        try:
            return int(parts[4])
        except ValueError:
            continue
    return 0


def process_image_name(pid: int) -> str:
    """取进程映像名，用于确认端口占用者确实是 node.exe。"""
    if not pid:
        return ""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=8,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    rows = list(csv.reader(io.StringIO(result.stdout or "")))
    if not rows or not rows[0]:
        return ""
    return str(rows[0][0]).strip()


def terminate_process(pid: int) -> bool:
    if not pid:
        return False
    try:
        result = subprocess.run(
            ["taskkill", "/PID", str(int(pid)), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=10,
            creationflags=_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class _WindowsJob:
    """把子进程放进 kill-on-close 作业对象，桌宠异常退出时由系统兜底结束。"""

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _BASIC_LIMIT(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    def __init__(self) -> None:
        self._handle: int | None = None
        self._kernel32: Any = None

    @classmethod
    def _extended(cls) -> Any:
        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", cls._BASIC_LIMIT),
                ("IoInfo", cls._IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        return Extended

    def open(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateJobObjectW.restype = ctypes.c_void_p
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return False
            extended_cls = self._extended()
            info = extended_cls()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                ctypes.c_void_p(handle),
                JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
                return False
            self._handle = int(handle)
            self._kernel32 = kernel32
            return True
        except Exception:
            self._handle = None
            self._kernel32 = None
            return False

    def assign(self, pid: int) -> None:
        if not self._handle or not self._kernel32 or not pid:
            return
        try:
            kernel32 = self._kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            process = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, int(pid)
            )
            if not process:
                return
            try:
                kernel32.AssignProcessToJobObject(
                    ctypes.c_void_p(self._handle), ctypes.c_void_p(process)
                )
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(process))
        except Exception:
            pass

    def close(self) -> None:
        handle = self._handle
        self._handle = None
        if handle and self._kernel32:
            try:
                self._kernel32.CloseHandle(ctypes.c_void_p(handle))
            except Exception:
                pass


class SnowLumaProcess:
    """一个隐藏窗口运行的 SnowLuma 实例。"""

    _MAX_LOG_BYTES = 4 * 1024 * 1024

    def __init__(self, folder: Path, node: str, log_path: Path) -> None:
        self.folder = folder
        self.node = node
        self.log_path = log_path
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
                    [self.node, "index.mjs"],
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

    def stop(self, timeout: float = 4.0) -> None:
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
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._job.close()


class SnowLumaManager:
    """按设置自动启动/停止 SnowLuma；只结束自己启动的实例。"""

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
        self._folder_setting = ""
        self._http = ""
        self._timeout = 90.0
        self._process: SnowLumaProcess | None = None
        self._thread: threading.Thread | None = None
        self._generation = 0
        self._stopping = True
        self._external = False

    # ---------- 配置 ----------

    def configure(self, settings: dict[str, Any], http: str | None = None) -> None:
        with self._lock:
            self._auto = bool(settings.get("auto_start_snowluma"))
            self._folder_setting = str(settings.get("snowluma_dir") or "")
            self._http = str(http or settings.get("snowluma_http") or "")

    def status(self) -> dict[str, Any]:
        with self._lock:
            process = self._process
            if process is not None and process.is_alive():
                return {"managed": True, "pid": process.pid, "alive": True}
            return {
                "managed": False,
                "pid": process.pid if process is not None else 0,
                "alive": False,
                "external": self._external,
            }

    # ---------- 启动 ----------

    def start_async(self, force: bool = False) -> None:
        with self._lock:
            if not self._auto and not force:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            if self._process is not None and self._process.is_alive():
                return
            self._stopping = False
            self._external = False
            generation = self._generation
            thread = threading.Thread(
                target=self.ensure_started,
                args=(generation, force),
                name="qq-bridge-snowluma",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def ensure_started(
        self, generation: int | None = None, force: bool = False
    ) -> None:
        """检查并启动；同步执行，线程和测试都可以直接调用。"""
        with self._lock:
            generation = self._generation if generation is None else generation
            if (not self._auto and not force) or self._stopping or generation != self._generation:
                return
            http = self._http
            folder_setting = self._folder_setting
            timeout = self._timeout

        host, port = url_endpoint(http, 3003)
        if port_open(host, port):
            self._set_external("SnowLuma 已经在运行，插件不会接管这个实例。")
            return

        folder = detect_snowluma_dir(folder_setting)
        if folder is None:
            self._log(
                "没找到 SnowLuma 目录：需要包含 index.mjs 和 node.exe，请在连接页填写路径。"
            )
            return

        webui_host, webui_port = webui_endpoint(folder)
        if port_open(webui_host, webui_port):
            self._log(
                f"SnowLuma 正在运行，但 OneBot HTTP（{host}:{port}）还没有监听。"
                f"请打开 http://{webui_host}:{webui_port} 保存一次 OneBot 配置，"
                "或先从控制台退出 SnowLuma，再让插件自动启动。"
            )
            return

        # 二次确认，避免和刚启动的实例撞端口。
        if port_open(host, port):
            self._set_external("SnowLuma 已经在运行，插件不会接管这个实例。")
            return

        node = folder / "node.exe"
        node_path = str(node) if node.is_file() else (shutil.which("node") or "")
        if not node_path:
            self._log("没找到 node.exe，也没有在 PATH 里找到 node，无法启动 SnowLuma。")
            return

        process = SnowLumaProcess(
            folder, node_path, self.state_root / "snowluma-stdout.log"
        )
        try:
            pid = process.start()
        except OSError as exc:
            self._log(f"启动 SnowLuma 失败：{exc}")
            return

        with self._lock:
            if self._stopping or generation != self._generation:
                process.stop()
                return
            self._process = process

        self._log(f"SnowLuma 已在后台启动（PID {pid}），等待 OneBot 服务就绪…")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._stopping or generation != self._generation:
                    return
            if not process.is_alive():
                self._log(
                    f"SnowLuma 启动后退出（退出码 {process.exit_code()}），"
                    f"启动日志：{process.log_path}"
                )
                process.stop()
                with self._lock:
                    if self._process is process:
                        self._process = None
                return
            if port_open(host, port):
                self._log(f"SnowLuma 已就绪：http://{host}:{port}")
                callback = self._on_ready
                if callback is not None:
                    try:
                        callback()
                    except Exception as exc:
                        self._log(f"SnowLuma 就绪回调失败：{exc}")
                return
            time.sleep(1.5)

        self._log(
            f"SnowLuma 已启动，但 {port} 端口在 {int(timeout)} 秒内没就绪；"
            f"打开 http://{webui_host}:{webui_port} 看看 QQ 是否已经登录。"
        )

    def _set_external(self, message: str) -> None:
        with self._lock:
            self._external = True
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
            self._log(f"关闭 SnowLuma 失败：{exc}")
            return
        self._log(f"已关闭由插件启动的 SnowLuma（PID {pid}）。")

    # ---------- 接管 ----------

    def takeover(self) -> str:
        """结束旧的 SnowLuma（含手动启动的实例），再由插件后台启动。"""
        with self._lock:
            self._generation += 1
            self._stopping = True
            process = self._process
            self._process = None
            folder_setting = self._folder_setting
            http = self._http
        if process is not None:
            pid = process.pid
            try:
                process.stop()
                self._log(f"已关闭由插件启动的 SnowLuma（PID {pid}）。")
            except Exception as exc:
                self._log(f"关闭 SnowLuma 失败：{exc}")
                with self._lock:
                    self._stopping = False
                return "关闭旧的 SnowLuma 失败，请手动退出后再试。"

        folder = detect_snowluma_dir(folder_setting)
        webui_port = webui_endpoint(folder)[1] if folder else 5099
        host, port = url_endpoint(http or "http://127.0.0.1:3003")
        killed: set[int] = set()
        for candidate in {webui_port, port}:
            pid = pid_listening_on(candidate)
            if not pid or pid in killed:
                continue
            name = process_image_name(pid)
            if name.lower() != "node.exe":
                self._log(
                    f"端口 {candidate} 被 {name or '未知进程'} 占用，已停止接管。"
                )
                with self._lock:
                    self._stopping = False
                return f"端口 {candidate} 被 {name or '其他程序'} 占用，没有继续操作。"
            if not terminate_process(pid):
                self._log(f"没能结束外部 SnowLuma 进程（PID {pid}）。")
                with self._lock:
                    self._stopping = False
                return "没能结束外部 SnowLuma 进程，请手动退出后再试。"
            killed.add(pid)
            self._log(f"已结束外部 SnowLuma 进程（PID {pid}）。")

        if killed:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if not port_open(host, port) and not port_open("127.0.0.1", webui_port):
                    break
                time.sleep(0.4)

        with self._lock:
            self._stopping = False
        self.start_async(force=True)
        return "已结束旧的 SnowLuma，正在后台重新启动。"
