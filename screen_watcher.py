from __future__ import annotations

import base64
import ctypes
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QImage

from ai_client import (
    AIRequest,
    SYSTEM_PROMPT,
    looks_like_vision_rejection,
    supports_vision,
)
from app_paths import ASSET_DIR
from config import Config
from conversation import Conversation


SCREEN_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + "\n\n【屏幕观察】你刚刚悄悄看了一眼主人的屏幕。"
    "请根据给你的窗口信息或截图，主动说一句自然、简短的话，像陪在旁边的猫娘随口一提。\n"
    "规则：\n"
    "1. 只说 1 到 2 句，不要写列表，不要像监控报告。\n"
    "2. 只挑一个安全的细节，比如主人在用什么软件、像是在做什么类型的事、桌面氛围。\n"
    "3. 绝对不要复述密码、账号、验证码、聊天内容、文件名、网址、代码、邮件内容等隐私细节。\n"
    "4. 不要声称自己看到了具体文字；不确定就说得模糊一点。\n"
    "5. 如果画面明显涉及隐私、工作机密，或者信息不足，只回两个字：SKIP。"
)

SENSITIVE_TITLE_WORDS = (
    "密码",
    "password",
    "passwd",
    "登录",
    "sign in",
    "log in",
    "验证码",
    "支付",
    "付款",
    "银行",
    "网银",
    "钱包",
    "wallet",
    "私钥",
    "密钥",
    "secret",
    "token",
    "身份证",
    "证件",
    "无痕",
    "incognito",
    "inprivate",
    "私密",
    "隐私",
    "远程桌面",
    "remote desktop",
)

REDACT_WORDS = (
    "密码",
    "password",
    "passwd",
    "验证码",
    "token",
    "secret",
    "api key",
    "apikey",
    "密钥",
    "银行卡",
    "身份证",
    "手机号",
    "私钥",
)

SENSITIVE_PROCESSES = {
    "keepass",
    "keepassxc",
    "1password",
    "bitwarden",
    "lastpass",
    "mstsc",
    "teamviewer",
    "anydesk",
    "rustdesk",
    "vmware",
    "virtualbox",
}


@dataclass
class ScreenContext:
    title: str = ""
    process: str = ""
    fullscreen: bool = False
    sensitive: bool = False
    image_jpeg: bytes = b""
    width: int = 0
    height: int = 0
    ocr_text: str = ""
    notes: list[str] = field(default_factory=list)
    mode: str = ""
    skip: bool = False
    skip_reason: str = ""

    def data_uri(self) -> str:
        if not self.image_jpeg:
            return ""
        encoded = base64.b64encode(self.image_jpeg).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def summary(self) -> str:
        parts = [f"窗口：{self.title or '未知'}"]
        if self.process:
            parts.append(f"程序：{self.process}")
        if self.ocr_text:
            parts.append(f"识别到 {len(self.ocr_text)} 个字符")
        if self.notes:
            parts.append(f"{len(self.notes)} 条插件补充")
        return "；".join(parts)


@dataclass
class WindowInfo:
    hwnd: int = 0
    title: str = ""
    process: str = ""
    fullscreen: bool = False

    @property
    def sensitive(self) -> bool:
        title = self.title.lower()
        process = self.process.lower()
        if process in SENSITIVE_PROCESSES:
            return True
        return any(word in title for word in SENSITIVE_TITLE_WORDS)


def _redact_sensitive_text(text: str) -> str:
    """OCR 结果里整行命中敏感词时直接丢弃，避免把密码行发给 AI。"""
    kept: list[str] = []
    for line in (text or "").splitlines():
        lowered = line.lower()
        if any(word in lowered for word in REDACT_WORDS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _foreground_window() -> WindowInfo:
    if os.name != "nt":
        return WindowInfo()
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return WindowInfo()

        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process = ""
        handle = kernel32.OpenProcess(0x1000, False, int(pid.value))
        if handle:
            try:
                path_buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(len(path_buffer))
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, path_buffer, ctypes.byref(size)
                ):
                    process = Path(path_buffer.value).stem.lower()
            finally:
                kernel32.CloseHandle(handle)

        fullscreen = False
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            monitor = user32.MonitorFromWindow(hwnd, 2)
            if monitor:
                class MONITORINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", wintypes.DWORD),
                        ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD),
                    ]

                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    mon = info.rcMonitor
                    tolerance = 2
                    fullscreen = (
                        abs(rect.left - mon.left) <= tolerance
                        and abs(rect.top - mon.top) <= tolerance
                        and abs(rect.right - mon.right) <= tolerance
                        and abs(rect.bottom - mon.bottom) <= tolerance
                    )
        return WindowInfo(hwnd=hwnd, title=title, process=process, fullscreen=fullscreen)
    except Exception:
        return WindowInfo()


def _encode_jpeg(image: QImage, quality: int) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", max(40, min(90, int(quality))))
    data = bytes(buffer.data())
    buffer.close()
    return data


def capture_screen(max_edge: int = 1280, quality: int = 68) -> ScreenContext:
    """截取主屏幕并压成 JPEG。只在主线程调用。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return ScreenContext()
    try:
        pixmap = screen.grabWindow(0)
        image = pixmap.toImage()
    except Exception:
        return ScreenContext()
    if image.isNull():
        return ScreenContext()
    width, height = image.width(), image.height()
    edge = max(480, min(1920, int(max_edge)))
    longest = max(width, height)
    if longest > edge:
        from PySide6.QtCore import Qt

        scale = edge / float(longest)
        image = image.scaled(
            max(1, int(width * scale)),
            max(1, int(height * scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    data = _encode_jpeg(image, int(quality))
    return ScreenContext(
        image_jpeg=data,
        width=image.width(),
        height=image.height(),
    )


class OcrSignals(QObject):
    done = Signal(str, str)  # text, error


class OcrTask:
    """在后台调用 Windows 自带 OCR，避免卡住桌面宠物的主线程。"""

    def __init__(self, image_jpeg: bytes) -> None:
        self.image_jpeg = image_jpeg
        self.signals = OcrSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        script = ASSET_DIR / "ocr_zh.ps1"
        if not script.exists():
            self.signals.done.emit("", "缺少 OCR 脚本")
            return
        public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
        root = public / "LFELKEEA" / "ocr"
        try:
            root.mkdir(parents=True, exist_ok=True)
            temp_dir = Path(tempfile.mkdtemp(prefix="shot_", dir=root))
        except Exception:
            temp_dir = Path(tempfile.mkdtemp(prefix="lfelkeea_ocr_"))
        image_path = temp_dir / "screen.jpg"
        output_path = temp_dir / "text.txt"
        try:
            image_path.write_bytes(self.image_jpeg)
            powershell = shutil.which("powershell.exe") or "powershell.exe"
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            command = [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-STA",
                "-WindowStyle",
                "Hidden",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ImagePath",
                str(image_path),
                "-OutputPath",
                str(output_path),
            ]
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                creationflags=flags,
            )
            text = ""
            if output_path.exists():
                text = output_path.read_text(encoding="utf-8", errors="replace").strip()
            if self._cancelled:
                return
            if result.returncode == 0 and text:
                self.signals.done.emit(text[:4000], "")
            elif result.returncode == 3 or not text:
                self.signals.done.emit("", "")
            else:
                self.signals.done.emit("", text[:160] or "OCR 失败")
        except subprocess.TimeoutExpired:
            if not self._cancelled:
                self.signals.done.emit("", "OCR 超时")
        except Exception as exc:
            if not self._cancelled:
                self.signals.done.emit("", f"OCR 失败：{exc}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class ScreenWatcher(QObject):
    """定时观察屏幕，并把观察结果变成桌宠的一句主动发言。"""

    status_changed = Signal(str)
    spoke = Signal(str)
    observed = Signal(object)

    def __init__(
        self,
        config: Config,
        pet: Any,
        conversation: Conversation,
        plugins: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pet = pet
        self.conversation = conversation
        self.plugins = plugins
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._busy = False
        self._token = 0
        self._ai: AIRequest | None = None
        self._ocr: OcrTask | None = None
        self._last_status = "屏幕观察未开启"
        self._last_comment = ""
        self._vision_blocked = False
        self._consecutive_skip = 0

    def settings(self) -> dict[str, Any]:
        value = self.config.get("screen_watch", {})
        return dict(value) if isinstance(value, dict) else {}

    @property
    def last_status(self) -> str:
        return self._last_status

    def _set_status(self, text: str) -> None:
        self._last_status = (text or "").strip()
        self.status_changed.emit(self._last_status)

    def _resolved_mode(self) -> str:
        settings = self.settings()
        mode = str(settings.get("mode", "auto"))
        if mode == "auto":
            if supports_vision(dict(self.config.ai)) and not self._vision_blocked:
                return "vision"
            return "window"
        if mode == "vision" and self._vision_blocked:
            return "window"
        return mode if mode in ("window", "vision", "ocr") else "window"

    def apply_settings(self) -> None:
        self._timer.stop()
        if self.settings().get("enabled", False):
            self._schedule()
            self._set_status("屏幕观察已开启，等待下一次观察")
        else:
            self._set_status("屏幕观察未开启")

    def _schedule(self, delay_ms: int | None = None) -> None:
        if not self.settings().get("enabled", False):
            self._timer.stop()
            return
        if delay_ms is None:
            interval = max(45, min(1800, int(self.settings().get("interval_seconds", 120))))
            delay_ms = int(interval * random.uniform(0.85, 1.15) * 1000)
        self._timer.start(max(15000, int(delay_ms)))

    def _on_timer(self) -> None:
        if self._busy:
            self._schedule()
            return
        self.observe(manual=False)

    def observe_now(self) -> None:
        self.observe(manual=True)

    def observe(self, manual: bool = False) -> None:
        settings = self.settings()
        if self._busy:
            if manual:
                self.pet.say("我还在看上一眼呢，等一下。", 3200)
            return
        if not manual and not settings.get("enabled", False):
            return

        if not manual:
            if getattr(self.pet, "_hidden_by_user", False) or getattr(self.pet, "_sleeping", False):
                self._schedule()
                return
            if getattr(self.pet, "_chat_open", False) or getattr(self.pet, "_voice_active", False):
                self._schedule()
                return
            idle = time.monotonic() - float(getattr(self.pet, "_last_interaction", 0.0))
            min_idle = max(0, int(settings.get("min_idle_seconds", 45)))
            if idle < min_idle:
                self._schedule()
                return

        info = _foreground_window()
        if settings.get("pause_sensitive", True) and info.sensitive:
            self._set_status("检测到隐私窗口，本次跳过")
            if manual:
                self.pet.say("这个窗口看起来涉及隐私，我就不看了。", 4200)
            self._schedule()
            return
        if settings.get("pause_when_fullscreen", True) and info.fullscreen:
            self._set_status("检测到全屏应用，本次跳过")
            if manual:
                self.pet.say("全屏的时候我容易看花眼，先不看了。", 4200)
            self._schedule()
            return

        context = ScreenContext(
            title=info.title,
            process=info.process,
            fullscreen=info.fullscreen,
            sensitive=info.sensitive,
        )
        # 先让插件补充信息或跳过，再决定是否截屏，避免白截一张隐私画面。
        self._run_plugins(context)
        self.observed.emit(context)
        if context.skip:
            self._set_status(context.skip_reason or "插件请求跳过本次观察")
            self._schedule()
            return

        mode = self._resolved_mode()
        if mode in ("vision", "ocr"):
            captured = self._capture(settings)
            if not captured.image_jpeg:
                self._set_status("截屏失败，改用窗口标题")
                mode = "window"
            else:
                context.image_jpeg = captured.image_jpeg
                context.width = captured.width
                context.height = captured.height
        context.mode = mode

        if mode == "ocr":
            self._start_ocr(context, manual)
            return
        self._request_comment(context, mode, manual)

    def _capture(self, settings: dict[str, Any]) -> ScreenContext:
        return capture_screen(
            max_edge=int(settings.get("max_edge", 1280)),
            quality=int(settings.get("jpeg_quality", 68)),
        )

    def _run_plugins(self, context: ScreenContext) -> None:
        if self.plugins is None:
            return
        try:
            self.plugins.emit("screen.context", context=context)
        except Exception:
            pass

    def _start_ocr(self, context: ScreenContext, manual: bool) -> None:
        self._busy = True
        self._set_status("正在识别屏幕文字…")
        token = self._token
        task = OcrTask(context.image_jpeg)
        self._ocr = task
        task.signals.done.connect(
            lambda text, error, t=token, c=context, m=manual: self._on_ocr(text, error, t, c, m)
        )
        task.start()

    def _on_ocr(
        self,
        text: str,
        error: str,
        token: int,
        context: ScreenContext,
        manual: bool,
    ) -> None:
        if token != self._token:
            return
        self._ocr = None
        self._busy = False
        if not text:
            note = f"（{error}）" if error else ""
            self._set_status(f"没有识别到文字{note}，改用窗口标题")
            context.ocr_text = ""
            self._request_comment(context, "window", manual)
            return
        context.ocr_text = _redact_sensitive_text(text)
        context.mode = "ocr"
        if not context.ocr_text:
            self._set_status("识别到的文字都涉及隐私，已跳过")
            self._schedule()
            return
        self._request_comment(context, "ocr", manual)

    def _user_prompt(self, context: ScreenContext, mode: str) -> str:
        lines = [
            f"当前时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"前台窗口标题：{context.title or '未知'}",
            f"前台程序：{context.process or '未知'}",
            f"窗口是否全屏：{'是' if context.fullscreen else '否'}",
        ]
        if mode == "ocr" and context.ocr_text:
            snippet = context.ocr_text
            if len(snippet) > 1600:
                snippet = snippet[:1600].rstrip() + "……"
            lines.append("屏幕文字（OCR，可能不完整，禁止复述隐私内容）：\n" + snippet)
        if context.notes:
            lines.append("插件补充：" + "；".join(str(n) for n in context.notes)[:600])
        if mode == "vision":
            lines.append("下面附上当前主屏幕的压缩截图。请只挑一个安全细节说一句。")
        else:
            lines.append("请根据这些信息说一句。信息不足就回 SKIP。")
        return "\n".join(lines)

    def _request_comment(self, context: ScreenContext, mode: str, manual: bool) -> None:
        self._busy = True
        self._token += 1
        token = self._token
        self._set_status("正在想怎么开口…")

        prompt = self._user_prompt(context, mode)
        if mode == "vision" and context.image_jpeg:
            content: Any = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": context.data_uri()}},
            ]
        else:
            content = prompt
        messages = [
            {"role": "system", "content": SCREEN_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        ai_config = dict(self.config.ai)
        if mode == "vision":
            ai_config["timeout"] = max(30, int(ai_config.get("timeout", 30) or 30))
        request = AIRequest(ai_config, messages)
        self._ai = request
        request.signals.ok.connect(
            lambda reply, t=token, c=context, m=mode, man=manual: self._on_ai_ok(reply, t, c, m, man)
        )
        request.signals.fail.connect(
            lambda error, t=token, c=context, m=mode, man=manual: self._on_ai_fail(error, t, c, m, man)
        )
        request.start()

    def _on_ai_ok(
        self,
        reply: str,
        token: int,
        context: ScreenContext,
        mode: str,
        manual: bool,
    ) -> None:
        if token != self._token:
            return
        self._ai = None
        self._busy = False
        text = (reply or "").strip().strip("“”\"'")
        if not text or text.upper().startswith("SKIP"):
            self._consecutive_skip += 1
            self._set_status("这次没找到适合开口的话题")
            self._schedule()
            return
        self._consecutive_skip = 0
        text = " ".join(text.split())
        if len(text) > 140:
            text = text[:140].rstrip() + "……"
        self._last_comment = text
        self._set_status(f"刚刚看了一眼屏幕：{text[:30]}{'…' if len(text) > 30 else ''}")
        self.pet.say(text, 7000)
        self.pet._trigger_bounce()
        self.conversation.append("assistant", text)
        self.spoke.emit(text)
        if self.plugins is not None:
            try:
                self.plugins.emit("screen.comment", text=text, context=context, mode=mode)
            except Exception:
                pass
        self._schedule()

    def _on_ai_fail(
        self,
        error: str,
        token: int,
        context: ScreenContext,
        mode: str,
        manual: bool,
    ) -> None:
        if token != self._token:
            return
        self._ai = None
        self._busy = False
        if (
            mode == "vision"
            and self.settings().get("vision_fallback", True)
            and looks_like_vision_rejection(error)
        ):
            self._vision_blocked = True
            self._set_status("当前模型不支持图片，已改用窗口标题")
            self._request_comment(context, "window", manual)
            return
        short = (error or "未知错误").strip()
        if len(short) > 80:
            short = short[:80] + "…"
        self._set_status(f"观察失败：{short}")
        if manual:
            self.pet.say("我这会儿看不清屏幕，等会儿再试吧。", 4200)
        self._schedule()

    def shutdown(self) -> None:
        self._token += 1
        self._timer.stop()
        if self._ai is not None:
            self._ai.cancel()
            self._ai = None
        if self._ocr is not None:
            self._ocr.cancel()
            self._ocr = None
        self._busy = False
