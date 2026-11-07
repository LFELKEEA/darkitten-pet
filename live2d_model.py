"""Live2D 模型渲染。

用 GLFW 创建一个隐藏窗口作为 OpenGL 上下文，在后台线程里按固定帧率渲染，
再把带透明通道的画面交给桌宠主窗口绘制。模型文件始终留在本机，不上传。

这里刻意不依赖 PyOpenGL 和 numpy：读回像素只用 opengl32 里的几个 1.1 接口，
色彩转换交给 Qt 的 QImage。
"""

from __future__ import annotations

import ctypes
import importlib
import math
import os
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtGui import QImage

_VENDOR_DIR = Path(__file__).resolve().parent / "vendor"
if _VENDOR_DIR.is_dir() and str(_VENDOR_DIR) not in sys.path:
    sys.path.insert(0, str(_VENDOR_DIR))

GL_RGBA = 0x1908
GL_UNSIGNED_BYTE = 0x1401
GL_PACK_ALIGNMENT = 0x0D05
GL_COLOR_BUFFER_BIT = 0x4000


class Live2DUnavailable(RuntimeError):
    pass


def _load_dependencies():
    try:
        glfw = importlib.import_module("glfw")
        live2d = importlib.import_module("live2d")
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise Live2DUnavailable(
            "当前环境没有 Live2D 运行库（live2d-py / glfw）。"
            "请把 desktop_pet/vendor 一起带上，或先安装 live2d-py 与 glfw。"
        ) from exc
    return glfw, live2d


def runtime_available() -> tuple[bool, str]:
    try:
        _load_dependencies()
        return True, ""
    except Live2DUnavailable as exc:
        return False, str(exc)


def _smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, value))
    return value * value * (3.0 - 2.0 * value)


class _Clip:
    """一段参数动画。keys 里每个关键帧只写需要变化的参数。"""

    def __init__(
        self,
        name: str,
        data: dict[str, Any],
        *,
        loop: bool = False,
        speed: float = 1.0,
        weight: float = 1.0,
        fade: float = 0.0,
    ) -> None:
        self.name = name
        self.duration = max(0.05, float(data.get("duration", 1.0)))
        self.loop = bool(data.get("loop", loop))
        self.speed = max(0.05, float(speed))
        self.target_weight = max(0.0, min(1.0, float(weight)))
        self.weight = 0.0 if fade > 0 else self.target_weight
        self.fade = max(0.0001, float(fade)) if fade > 0 else 0.0
        self.time = 0.0
        self.done = False
        self._tracks: dict[str, list[tuple[float, float]]] = {}
        keys = data.get("keys") or []
        for key in keys:
            if not isinstance(key, dict):
                continue
            try:
                at = float(key.get("t", 0.0))
            except (TypeError, ValueError):
                continue
            params = key.get("params")
            if not isinstance(params, dict):
                continue
            for pid, raw in params.items():
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                self._tracks.setdefault(str(pid), []).append((at, value))
        for pid, track in self._tracks.items():
            track.sort(key=lambda item: item[0])
        self.params = list(self._tracks)

    def restart(self) -> None:
        self.time = 0.0
        self.done = False
        if self.fade:
            self.weight = 0.0
        else:
            self.weight = self.target_weight

    def advance(self, dt: float) -> None:
        if self.done:
            return
        self.time += dt * self.speed
        if self.loop:
            if self.duration > 0:
                self.time %= self.duration
        elif self.time >= self.duration:
            self.time = self.duration
            self.done = True
        if self.fade and self.weight < self.target_weight:
            step = dt / self.fade * max(0.05, self.target_weight)
            self.weight = min(self.target_weight, self.weight + step)

    def fade_out(self, dt: float, speed: float = 2.6) -> bool:
        self.weight = max(0.0, self.weight - dt * speed)
        return self.weight <= 0.001

    def value_at(self, t: float) -> dict[str, float]:
        out: dict[str, float] = {}
        for pid, track in self._tracks.items():
            if not track:
                continue
            if t <= track[0][0]:
                out[pid] = track[0][1]
                continue
            if t >= track[-1][0]:
                out[pid] = track[-1][1]
                continue
            prev = track[0]
            for point in track[1:]:
                if point[0] >= t:
                    span = point[0] - prev[0]
                    ratio = 0.0 if span <= 0 else (t - prev[0]) / span
                    out[pid] = prev[1] + (point[1] - prev[1]) * _smoothstep(ratio)
                    break
                prev = point
        return out


def _read_pixels(x: int, y: int, width: int, height: int) -> bytes:
    gl = ctypes.WinDLL("opengl32")
    size = int(width) * int(height) * 4
    buffer = ctypes.create_string_buffer(size)
    gl.glPixelStorei(GL_PACK_ALIGNMENT, 1)
    gl.glReadPixels(int(x), int(y), int(width), int(height), GL_RGBA, GL_UNSIGNED_BYTE, buffer)
    return buffer.raw


def _clear_frame(width: int, height: int) -> None:
    """每帧先清空默认帧缓冲，否则动作会在画面上留下残影。"""
    gl = ctypes.WinDLL("opengl32")
    gl.glViewport.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    gl.glClearColor.argtypes = [ctypes.c_float] * 4
    gl.glClear.argtypes = [ctypes.c_uint]
    gl.glViewport(0, 0, int(width), int(height))
    gl.glClearColor(0.0, 0.0, 0.0, 0.0)
    gl.glClear(GL_COLOR_BUFFER_BIT)


def _alpha_bounds(raw: bytes, width: int, height: int, threshold: int = 8) -> tuple[int, int, int, int]:
    """在原始像素里找一个粗略的内容边界，返回 (x, y, w, h)，坐标原点在左下。"""
    step = 4
    min_x, min_y = width, height
    max_x, max_y = -1, -1
    for y in range(0, height, step):
        row = y * width * 4
        for x in range(0, width, step):
            if raw[row + x * 4 + 3] > threshold:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return 0, 0, width, height
    # 留一点余量，动作里头部/手臂小幅越界时不会被裁掉。
    pad = 18
    left = max(0, min_x - pad)
    bottom = max(0, min_y - pad)
    right = min(width, max_x + pad + step)
    top = min(height, max_y + pad + step)
    return left, bottom, max(1, right - left), max(1, top - bottom)


def _qimage(raw: bytes, width: int, height: int) -> QImage:
    image = QImage(
        raw,
        width,
        height,
        width * 4,
        QImage.Format.Format_RGBA8888_Premultiplied,
    )
    return image.mirrored(False, True)


class _RenderThread(QThread):
    frame_ready = Signal(QImage)
    ready = Signal(dict)
    failed = Signal(str)

    def __init__(
        self,
        model_path: str,
        width: int,
        height: int,
        fps: int,
        mask_count: int,
        scale: float,
        offset: tuple[float, float],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model_path = model_path
        self.width = max(64, int(width))
        self.height = max(64, int(height))
        self.fps = max(8, min(60, int(fps)))
        self.mask_count = int(mask_count)
        self.scale = float(scale)
        self.offset = (float(offset[0]), float(offset[1]))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._commands: list[tuple[str, Any]] = []
        self._clips: dict[str, dict[str, Any]] = {}
        self._expressions: dict[str, dict[str, Any]] = {}
        self._gaze = (0.0, 0.0)
        self._follow_cursor = False
        self._talking = False
        self._sleeping = False
        self._extra: dict[str, float] = {}

    # ---------- 主线程侧 ----------

    def stop(self) -> None:
        self._stop.set()

    def push(self, name: str, payload: Any = None) -> None:
        with self._lock:
            self._commands.append((name, payload))

    # ---------- 渲染线程 ----------

    def run(self) -> None:  # noqa: D401
        model = None
        window = None
        glfw = None
        live2d = None
        try:
            glfw, live2d = _load_dependencies()
            live2d.enableLog(False)
            if not glfw.init():
                raise Live2DUnavailable("GLFW 初始化失败，无法创建 OpenGL 上下文。")
            glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
            glfw.window_hint(glfw.FOCUS_ON_SHOW, glfw.FALSE)
            glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
            glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
            glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_COMPAT_PROFILE)
            window = glfw.create_window(self.height, self.height, "lfelkeea-live2d", None, None)
            if not window:
                raise Live2DUnavailable("无法创建隐藏的 OpenGL 窗口。")
            glfw.make_context_current(window)
            glfw.swap_interval(0)

            live2d.glInit()
            live2d.init()

            model = live2d.Model()
            model.LoadModelJson(self.model_path, create_renderer=False)
            canvas = model.GetCanvasSize()
            try:
                model.CreateRenderer(self.mask_count)
            except Exception:
                model.CreateRenderer(1)
            # 先用正方形画布，模型按高度自适应；第一帧之后再按内容裁掉透明边。
            model.Resize(self.height, self.height)
            model.SetAutoBreath(False)
            model.SetAutoBlink(False)
            if abs(self.scale - 1.0) > 0.001:
                try:
                    model.SetScale(self.scale)
                except Exception:
                    pass
            if abs(self.offset[0]) > 0.001 or abs(self.offset[1]) > 0.001:
                try:
                    model.SetOffsetX(self.offset[0])
                    model.SetOffsetY(self.offset[1])
                except Exception:
                    pass

            self._canvas = (float(canvas[0]), float(canvas[1]))
            self._run_loop(model)
        except Exception as exc:  # pragma: no cover - 运行环境相关
            self.failed.emit(str(exc))
        finally:
            try:
                if model is not None:
                    model.DestroyRenderer()
            except Exception:
                pass
            try:
                if live2d is not None:
                    live2d.glRelease()
                    live2d.dispose()
            except Exception:
                pass
            try:
                if window is not None and glfw is not None:
                    glfw.destroy_window(window)
                    glfw.terminate()
            except Exception:
                pass

    def _run_loop(self, model) -> None:
        frame_time = 1.0 / self.fps
        last = time.perf_counter()
        base_time = 0.0
        blink_at = time.perf_counter() + random.uniform(2.0, 5.0)
        blink_start = -1.0
        clips: list[_Clip] = []
        fading: list[_Clip] = []
        expression: _Clip | None = None
        expression_until = -1.0
        extra: dict[str, float] = {}
        crop: tuple[int, int, int, int] | None = None
        full = self.height
        params = set(model.GetParamIds())

        while not self._stop.is_set():
            started = time.perf_counter()
            dt = min(0.12, max(0.001, started - last))
            last = started
            base_time += dt

            # 主线程发来的指令
            with self._lock:
                commands = self._commands
                self._commands = []
                gaze = self._gaze
                follow = self._follow_cursor
                talking = self._talking
                sleeping = self._sleeping
                definitions = dict(self._clips)
                expression_defs = dict(self._expressions)
                extra = dict(self._extra)

            for name, payload in commands:
                if name == "clips":
                    with self._lock:
                        self._clips = dict(payload or {})
                    definitions = dict(self._clips)
                elif name == "expressions":
                    with self._lock:
                        self._expressions = dict(payload or {})
                    expression_defs = dict(self._expressions)
                elif name == "play":
                    data = definitions.get(str(payload.get("name", "")))
                    if data is None:
                        continue
                    clip = _Clip(
                        str(payload["name"]),
                        data,
                        loop=bool(payload.get("loop", data.get("loop", False))),
                        speed=float(payload.get("speed", 1.0)),
                        weight=float(payload.get("weight", 1.0)),
                        fade=float(payload.get("fade", 0.18 if not payload.get("loop") else 0.4)),
                    )
                    if payload.get("restart", True):
                        clips = [item for item in clips if item.name != clip.name]
                    clips.append(clip)
                elif name == "stop":
                    target = str(payload or "")
                    for item in clips:
                        if item.name == target:
                            fading.append(item)
                    clips = [item for item in clips if item.name != target]
                elif name == "stop_all":
                    fading.extend(clips)
                    clips = []
                    if expression is not None:
                        fading.append(expression)
                    expression = None
                    expression_until = -1.0
                elif name == "expression":
                    target = str(payload.get("name", ""))
                    data = expression_defs.get(target)
                    if data is None:
                        continue
                    if expression is not None:
                        fading.append(expression)
                    expression = _Clip(
                        target,
                        data,
                        loop=True,
                        weight=1.0,
                        fade=max(0.05, float(payload.get("fade", 0.3))),
                    )
                    hold = float(payload.get("hold", 0.0) or 0.0)
                    expression_until = (base_time + hold) if hold > 0 else -1.0
                elif name == "clear_expression":
                    if expression is not None:
                        fading.append(expression)
                    expression = None
                    expression_until = -1.0

            for item in list(clips):
                item.advance(dt)
                if item.done and not item.loop:
                    clips.remove(item)
                    fading.append(item)
            for item in list(fading):
                if item.fade_out(dt):
                    fading.remove(item)
            if expression is not None:
                expression.advance(dt)
                if expression_until > 0 and base_time >= expression_until:
                    fading.append(expression)
                    expression = None
                    expression_until = -1.0

            pose = self._base_pose(base_time, gaze, follow, talking, sleeping, blink_start, blink_at)
            if started - blink_at >= 0:
                blink_start = started
                blink_at = started + random.uniform(2.2, 6.5)
            if blink_start >= 0:
                phase = (started - blink_start) / 0.16
                if phase >= 1.0:
                    blink_start = -1.0
                else:
                    closed = math.sin(phase * math.pi)
                    pose["ParamEyeLOpen"] *= 1.0 - closed
                    pose["ParamEyeROpen"] *= 1.0 - closed

            for pid, value in extra.items():
                pose[pid] = value

            # 覆盖层：表情 → 动作片段（后者优先）
            if expression is not None:
                for pid, value in expression.value_at(expression.time).items():
                    base = pose.get(pid, 0.0)
                    pose[pid] = base * (1.0 - expression.weight) + value * expression.weight
            for layer in fading + clips:
                values = layer.value_at(layer.time)
                for pid, value in values.items():
                    base = pose.get(pid, 0.0)
                    pose[pid] = base * (1.0 - layer.weight) + value * layer.weight

            try:
                # 每帧从模型默认姿势开始，避免上一帧的装饰参数残留。
                model.ResetAllParameters()
                model.Update(dt)
                for pid, value in pose.items():
                    model.SetParamById(pid, float(value))
                model.UpdatePhysics(dt)
                _clear_frame(full, full)
                model.Draw()
            except Exception as exc:  # 渲染一帧失败不终止整个线程
                self.failed.emit(f"渲染失败：{exc}")
                self._stop.set()
                break

            try:
                raw = _read_pixels(0, 0, full, full) if crop is None else _read_pixels(*crop)
                if crop is None:
                    image = _qimage(raw, full, full)
                    left, bottom, width, height = _alpha_bounds(raw, full, full)
                    crop = (left, bottom, width, height)
                    self.ready.emit(
                        {
                            "canvas": self._canvas,
                            "params": params,
                            "width": width,
                            "height": height,
                        }
                    )
                    top = full - (bottom + height)
                    image = image.copy(max(0, left), max(0, top), width, height)
                else:
                    image = _qimage(raw, crop[2], crop[3])
                self.frame_ready.emit(image)
            except Exception as exc:
                self.failed.emit(f"读取画面失败：{exc}")
                self._stop.set()
                break

            elapsed = time.perf_counter() - started
            wait = frame_time - elapsed
            if wait > 0:
                time.sleep(wait)

    def _base_pose(
        self,
        t: float,
        gaze: tuple[float, float],
        follow: bool,
        talking: bool,
        sleeping: bool,
        blink_start: float,
        blink_at: float,
    ) -> dict[str, float]:
        sway = math.sin(t * 0.42)
        drift = math.sin(t * 0.17 + 1.2)
        breathe = math.sin(t * 1.55)
        pose: dict[str, float] = {
            "ParamBreath": 0.5 + 0.5 * breathe,
            "ParamAngleX": 2.4 * sway + 1.1 * drift,
            "ParamAngleY": 1.2 * math.sin(t * 0.31 + 2.0) - 0.6 * breathe,
            "ParamAngleZ": 1.7 * math.sin(t * 0.27 + 3.0),
            "ParamBodyAngleX": 1.5 * sway,
            "ParamBodyAngleZ": 1.2 * math.sin(t * 0.27 + 3.0),
            "ParamEyeBallX": 0.0,
            "ParamEyeBallY": 0.0,
            "PupilX": 0.0,
            "PupilY": 0.0,
            "ParamEyeLOpen": 1.0,
            "ParamEyeROpen": 1.0,
            "ParamMouthOpenY": 0.0,
            "ParamMouthForm": 0.0,
            "ParamBrowLY": 0.0,
            "ParamBrowRY": 0.0,
            "CheeckPuff": 0.0,
            "CheeckPuff2": 0.0,
        }
        if follow:
            gx, gy = gaze
            pose["ParamEyeBallX"] = 0.72 * gx
            pose["ParamEyeBallY"] = 0.62 * gy
            pose["PupilX"] = 0.55 * gx
            pose["PupilY"] = 0.45 * gy
            pose["ParamAngleX"] += 3.4 * gx
            pose["ParamAngleY"] -= 2.2 * gy
            pose["ParamBodyAngleX"] += 1.3 * gx
        if talking:
            flutter = abs(math.sin(t * 12.5)) * (0.55 + 0.45 * abs(math.sin(t * 3.3)))
            pose["ParamMouthOpenY"] = 0.08 + 0.34 * flutter
            pose["ParamMouthForm"] = 0.18
            pose["ParamAngleZ"] += math.sin(t * 6.0) * 0.9
        if sleeping:
            pose["ParamEyeLOpen"] = 0.0
            pose["ParamEyeROpen"] = 0.0
            pose["ParamAngleZ"] += 6.0
            pose["ParamAngleY"] += 3.5
            pose["ParamBodyAngleZ"] += 2.0
            pose["ParamMouthOpenY"] = 0.06 + 0.02 * math.sin(t * 0.9)
            pose["ParamBrowLY"] = 0.18
            pose["ParamBrowRY"] = 0.18
        return pose


class Live2DController(QObject):
    """桌宠侧的模型控制器，主线程使用。"""

    frame_ready = Signal(QImage)
    loaded = Signal(dict)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: _RenderThread | None = None
        self._loaded = False
        self._info: dict[str, Any] = {}
        self._owner = ""
        self._path = ""
        self._last_error = ""
        self._scale = 1.0
        self._offset = (0.0, 0.0)
        self._fps = 30
        self._auto_gaze = False
        self._mask_count = 13
        self._width = 360
        self._height = 740
        self._resizing = False

    # ---------- 状态 ----------

    @property
    def is_active(self) -> bool:
        return self._loaded and self._thread is not None and self._thread.isRunning()

    @property
    def owner(self) -> str:
        return self._owner

    @property
    def info(self) -> dict[str, Any]:
        return dict(self._info)

    @property
    def last_error(self) -> str:
        return self._last_error

    @property
    def aspect(self) -> float:
        canvas = self._info.get("canvas") or (1.0, 2.0)
        try:
            return float(canvas[0]) / max(0.001, float(canvas[1]))
        except (TypeError, ValueError):
            return 0.5

    @property
    def auto_gaze(self) -> bool:
        return self._auto_gaze

    # ---------- 生命周期 ----------

    def load(
        self,
        model_path: str,
        *,
        owner: str = "",
        height: int = 720,
        fps: int = 30,
        scale: float = 1.0,
        offset: tuple[float, float] = (0.0, 0.0),
    ) -> bool:
        if self.is_active:
            self.unload()
        path = Path(str(model_path)).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"找不到模型文件：{path}")
        ok, reason = runtime_available()
        if not ok:
            raise Live2DUnavailable(reason)

        # 先用正方形画布渲染，线程会按模型实际内容裁掉透明边。
        self._height = max(240, min(1600, int(height)))
        self._width = self._height
        self._fps = max(10, min(60, int(fps)))
        self._scale = float(scale)
        self._offset = (float(offset[0]), float(offset[1]))
        thread = _RenderThread(
            str(path),
            self._width,
            self._height,
            self._fps,
            self._mask_count,
            self._scale,
            self._offset,
            self,
        )
        thread.frame_ready.connect(self._on_frame)
        thread.ready.connect(self._on_ready)
        thread.failed.connect(self._on_failed)
        self._thread = thread
        self._owner = str(owner or "")
        self._path = str(path)
        self._last_error = ""
        thread.start()
        return True

    def unload(self) -> None:
        thread = self._thread
        self._thread = None
        self._loaded = False
        self._info = {}
        self._owner = ""
        self._auto_gaze = False
        if thread is not None:
            thread.stop()
            thread.wait(2500)
            if thread.isRunning():
                thread.terminate()
                thread.wait(800)
        self.failed.emit("")

    # ---------- 渲染线程回调 ----------

    def _on_ready(self, info: dict) -> None:
        self._info = dict(info)
        self._loaded = True
        self.loaded.emit(self.info)

    def _on_frame(self, image: QImage) -> None:
        if image.isNull():
            return
        self.frame_ready.emit(image)

    def _on_failed(self, message: str) -> None:
        text = str(message or "").strip()
        if not text:
            return
        self._last_error = text
        self._loaded = False
        self.failed.emit(text)

    # ---------- 指令 ----------

    def _live_thread(self) -> _RenderThread | None:
        thread = self._thread
        if thread is None or not thread.isRunning():
            return None
        return thread

    def define_clips(self, clips: dict[str, Any]) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push("clips", dict(clips or {}))

    def define_expressions(self, expressions: dict[str, Any]) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push("expressions", dict(expressions or {}))

    def play(
        self,
        name: str,
        *,
        loop: bool = False,
        speed: float = 1.0,
        weight: float = 1.0,
        restart: bool = True,
        fade: float = 0.18,
    ) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push(
                "play",
                {
                    "name": str(name),
                    "loop": bool(loop),
                    "speed": float(speed),
                    "weight": float(weight),
                    "restart": bool(restart),
                    "fade": float(fade),
                },
            )

    def stop(self, name: str) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push("stop", str(name))

    def stop_all(self) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push("stop_all", None)

    def set_expression(self, name: str, fade: float = 0.3, hold: float = 0.0) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push(
                "expression",
                {"name": str(name), "fade": float(fade), "hold": float(hold)},
            )

    def clear_expression(self) -> None:
        thread = self._live_thread()
        if thread is not None:
            thread.push("clear_expression", None)

    def set_gaze(self, x: float, y: float) -> None:
        thread = self._live_thread()
        if thread is None:
            return
        with thread._lock:  # noqa: SLF001 - 同一模块内的状态同步
            thread._gaze = (
                max(-1.0, min(1.0, float(x))),
                max(-1.0, min(1.0, float(y))),
            )

    def set_follow_cursor(self, enabled: bool) -> None:
        thread = self._live_thread()
        self._auto_gaze = bool(enabled)
        if thread is None:
            return
        with thread._lock:  # noqa: SLF001
            thread._follow_cursor = bool(enabled)

    def set_talking(self, talking: bool) -> None:
        thread = self._live_thread()
        if thread is None:
            return
        with thread._lock:  # noqa: SLF001
            thread._talking = bool(talking)

    def set_sleeping(self, sleeping: bool) -> None:
        thread = self._live_thread()
        if thread is None:
            return
        with thread._lock:  # noqa: SLF001
            thread._sleeping = bool(sleeping)

    def set_params(self, params: dict[str, float]) -> None:
        thread = self._live_thread()
        if thread is None:
            return
        clean: dict[str, float] = {}
        for key, value in (params or {}).items():
            try:
                clean[str(key)] = float(value)
            except (TypeError, ValueError):
                continue
        with thread._lock:  # noqa: SLF001
            thread._extra = clean
