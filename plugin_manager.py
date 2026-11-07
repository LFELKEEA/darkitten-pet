from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot

from ai_client import AIRequest, SYSTEM_PROMPT
from app_paths import DATA_DIR, resource_dir
from config import Config
from plugin_updater import (
    REGISTRY_URL,
    PluginRelease,
    check_updates as check_plugin_releases,
    download_plugin,
    fetch_registry,
    install_plugin,
    releases_from_registry,
)
from screen_watcher import ScreenContext, capture_screen
from system_utils import move_to_trash


API_VERSION = 1
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")

PERMISSION_LABELS = {
    "pet.say": "让桌宠说话",
    "pet.action": "触发桌宠动作",
    "pet.model": "替换桌宠模型与模型动作",
    "pet.persona": "替换桌宠内置台词",
    "ai.chat": "调用 AI 对话",
    "chat.context": "向聊天注入上下文",
    "screen.capture": "截取屏幕",
    "timer": "创建定时器",
    "events": "订阅事件",
    "settings": "读写插件设置",
    "storage": "读写插件存储",
}

KNOWN_EVENTS = {
    "startup",
    "shutdown",
    "settings.saved",
    "chat.user",
    "chat.reply",
    "screen.context",
    "screen.comment",
    "pet.pat",
    "pet.poke",
}


class PluginError(Exception):
    pass


class PluginPermissionError(PluginError):
    pass


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    api_version: int
    entry: str
    description: str = ""
    author: str = ""
    permissions: list[str] = field(default_factory=list)
    folder: Path = Path()
    enabled: bool = False
    status: str = "未启用"
    error: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)
    latest_version: str = ""

    @property
    def has_update(self) -> bool:
        return bool(self.latest_version)

    @property
    def permissions_text(self) -> str:
        if not self.permissions:
            return "无特殊权限"
        return "、".join(PERMISSION_LABELS.get(p, p) for p in self.permissions)


@dataclass
class LoadedPlugin:
    info: PluginInfo
    module: Any
    api: "PluginAPI"


class _ChatBridge(QObject):
    """把后台 AI 线程的结果安全地投递回主线程。"""

    def __init__(self, api: "PluginAPI", token: int, callback: Callable[..., Any]) -> None:
        super().__init__(api)
        self.api = api
        self.token = token
        self.callback = callback

    @Slot(str)
    def ok(self, reply: str) -> None:
        self.api._finish_chat(self.token, reply, None)

    @Slot(str)
    def fail(self, error: str) -> None:
        self.api._finish_chat(self.token, None, error)


class PetModelAPI:
    """Live2D 模型接口。所有调用都在桌宠主线程完成。"""

    def __init__(self, api: "PluginAPI") -> None:
        self.api = api

    # ---------- 内部 ----------

    def _pet(self):
        self.api._require("pet.model")
        self.api._assert_main_thread()
        return self.api.manager.pet

    def _controller(self):
        pet = self._pet()
        controller = getattr(pet, "_live2d", None)
        if controller is None or not controller.is_active:
            raise PluginError("模型还没有加载，请先调用 api.model.load(path)。")
        return controller

    # ---------- 加载 ----------

    @property
    def available(self) -> bool:
        try:
            from live2d_model import runtime_available

            return runtime_available()[0]
        except Exception:
            return False

    def load(
        self,
        path: str,
        *,
        height: int = 720,
        fps: int = 30,
        scale: float = 1.0,
        offset: tuple[float, float] = (0.0, 0.0),
        follow_cursor: bool = True,
    ) -> bool:
        pet = self._pet()
        model_path = str(path or "").strip()
        if not model_path:
            raise PluginError("model.load 需要一个 .model3.json 路径。")
        return bool(
            pet.load_live2d(
                model_path,
                owner=self.api.info.id,
                height=max(240, min(1600, int(height))),
                fps=max(10, min(60, int(fps))),
                scale=float(scale),
                offset=(float(offset[0]), float(offset[1])),
                follow_cursor=bool(follow_cursor),
            )
        )

    def unload(self) -> bool:
        return bool(self._pet().unload_live2d(self.api.info.id))

    @property
    def info(self) -> dict[str, Any]:
        return self._pet().model_info()

    # ---------- 动作 ----------

    def define_clips(self, clips: dict[str, Any]) -> None:
        if not isinstance(clips, dict):
            raise PluginError("define_clips 需要 {名字: 片段} 形式的字典。")
        self._controller().define_clips(clips)

    def define_expressions(self, expressions: dict[str, Any]) -> None:
        if not isinstance(expressions, dict):
            raise PluginError("define_expressions 需要 {名字: 参数} 形式的字典。")
        self._controller().define_expressions(expressions)

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
        self._controller().play(
            str(name),
            loop=bool(loop),
            speed=float(speed),
            weight=float(weight),
            restart=bool(restart),
            fade=float(fade),
        )

    def stop(self, name: str) -> None:
        self._controller().stop(str(name))

    def stop_all(self) -> None:
        self._controller().stop_all()

    def expression(self, name: str, *, fade: float = 0.3, hold: float = 0.0) -> None:
        self._controller().set_expression(str(name), fade=float(fade), hold=float(hold))

    def clear_expression(self) -> None:
        self._controller().clear_expression()

    def gaze(self, x: float, y: float) -> None:
        self._controller().set_gaze(float(x), float(y))

    def follow_cursor(self, enabled: bool = True) -> None:
        self._controller().set_follow_cursor(bool(enabled))

    def talk(self, enabled: bool) -> None:
        self._controller().set_talking(bool(enabled))

    def sleep(self, enabled: bool) -> None:
        self._controller().set_sleeping(bool(enabled))

    def params(self, values: dict[str, float]) -> None:
        if not isinstance(values, dict):
            raise PluginError("params 需要 {参数名: 数值} 形式的字典。")
        self._controller().set_params(values)


class PluginAPI(QObject):
    """暴露给插件作者的受控接口。

    所有回调都在桌宠主线程执行；插件不应自己创建线程直接操作 Qt 界面。
    """

    def __init__(self, manager: "PluginManager", info: PluginInfo) -> None:
        super().__init__(manager)
        self.manager = manager
        self.info = info
        self.version = API_VERSION
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._timers: list[QTimer] = []
        self._requests: dict[int, AIRequest] = {}
        self._callbacks: dict[int, Callable[..., Any]] = {}
        self._bridges: dict[int, _ChatBridge] = {}
        self._request_seq = 0
        self._storage: dict[str, Any] | None = None
        self._model_api: "PetModelAPI | None" = None

    # ---------- 基础工具 ----------

    def _require(self, permission: str) -> None:
        if permission not in self.info.permissions:
            raise PluginPermissionError(
                f"插件 {self.info.id} 没有声明权限 {permission}，请在 plugin.json 的 permissions 中加入。"
            )

    def _assert_main_thread(self) -> None:
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() is not app.thread():
            raise PluginError("该接口只能在桌宠主线程调用。")

    def _safe(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        try:
            callback(*args, **kwargs)
        except Exception as exc:  # 插件异常不能影响桌宠
            self.manager.record_error(self.info, exc, "回调")

    # ---------- 桌宠 ----------

    def say(self, text: str, duration: int = 5000) -> None:
        self._require("pet.say")
        self._assert_main_thread()
        message = " ".join(str(text or "").split())
        if not message:
            return
        self.manager.pet.say(message[:200], max(1200, min(20000, int(duration))))

    def action(self, name: str) -> None:
        self._require("pet.action")
        self._assert_main_thread()
        specs = getattr(self.manager.pet, "ACTION_SPECS", {})
        if name not in specs:
            raise PluginError(f"未知动作：{name}")
        self.manager.pet.start_action(name, announce=True)

    @property
    def model(self) -> PetModelAPI:
        self._require("pet.model")
        if self._model_api is None:
            self._model_api = PetModelAPI(self)
        return self._model_api

    def set_lines(self, callback: Callable[[str, tuple[str, ...]], str]) -> None:
        """替换桌宠的内置台词（闲聊、摸头、戳一戳、动作、睡觉等）。

        callback(kind, choices) 返回要显示的文本；返回空字符串则沿用原台词。
        """
        self._require("pet.persona")
        self._assert_main_thread()
        if not callable(callback):
            raise PluginError("set_lines 的 callback 必须可调用。")
        self.manager.pet.set_line_override(callback, self.info.id)

    def clear_lines(self) -> None:
        self._require("pet.persona")
        self._assert_main_thread()
        self.manager.pet.clear_line_override(self.info.id)

    # ---------- AI ----------

    def chat(
        self,
        messages: str | dict[str, Any] | list[dict[str, Any]],
        callback: Callable[[str | None, str | None], None],
    ) -> None:
        """异步请求 AI。callback(reply, error)，二选一为 None。"""
        self._require("ai.chat")
        self._assert_main_thread()
        if not callable(callback):
            raise PluginError("chat 的 callback 必须可调用。")
        if isinstance(messages, str):
            payload: list[dict[str, Any]] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": messages[:4000]},
            ]
        elif isinstance(messages, dict):
            payload = [{"role": "system", "content": SYSTEM_PROMPT}, dict(messages)]
        elif isinstance(messages, list):
            payload = [dict(item) for item in messages if isinstance(item, dict)]
            if not payload:
                raise PluginError("messages 不能为空。")
        else:
            raise PluginError("messages 只支持字符串、字典或列表。")

        request = AIRequest(dict(self.manager.config.ai), payload)
        self._request_seq += 1
        token = self._request_seq
        self._requests[token] = request
        self._callbacks[token] = callback
        bridge = _ChatBridge(self, token, callback)
        self._bridges[token] = bridge
        request.signals.ok.connect(bridge.ok)
        request.signals.fail.connect(bridge.fail)
        request.start()

    def _finish_chat(self, token: int, reply: str | None, error: str | None) -> None:
        self._requests.pop(token, None)
        bridge = self._bridges.pop(token, None)
        callback = self._callbacks.pop(token, None)
        if bridge is not None:
            bridge.deleteLater()
        if callback is not None:
            self._safe(callback, reply, error)

    # ---------- 屏幕 ----------

    def capture_screen(self) -> ScreenContext:
        self._require("screen.capture")
        self._assert_main_thread()
        return self.manager.capture_screen()

    # ---------- 定时器与事件 ----------

    def every(self, interval_ms: int, callback: Callable[[], Any]) -> None:
        self._require("timer")
        self._assert_main_thread()
        if not callable(callback):
            raise PluginError("every 的 callback 必须可调用。")
        interval = max(1000, min(24 * 60 * 60 * 1000, int(interval_ms)))
        timer = QTimer(self)
        timer.setInterval(interval)
        timer.timeout.connect(lambda: self._safe(callback))
        timer.start()
        self._timers.append(timer)

    def after(self, delay_ms: int, callback: Callable[[], Any]) -> None:
        self._require("timer")
        self._assert_main_thread()
        if not callable(callback):
            raise PluginError("after 的 callback 必须可调用。")
        delay = max(0, min(24 * 60 * 60 * 1000, int(delay_ms)))
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(delay)
        timer.timeout.connect(lambda: self._safe(callback))
        timer.start()
        self._timers.append(timer)

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self._require("events")
        self._assert_main_thread()
        name = str(event or "").strip()
        if name not in KNOWN_EVENTS:
            raise PluginError(f"未知事件：{name}")
        if not callable(callback):
            raise PluginError("on 的 callback 必须可调用。")
        self._handlers.setdefault(name, []).append(callback)

    def _dispatch(self, event: str, payload: dict[str, Any]) -> None:
        for callback in list(self._handlers.get(event, [])):
            self._safe(callback, **payload)

    # ---------- 聊天上下文 ----------

    def register_prompt_provider(self, callback: Callable[..., Any]) -> Callable[..., Any]:
        """注册一个上下文提供者，返回可传给 unregister 的句柄。

        callback(query=..., viewer=..., source=..., channel=...) -> str
        """
        self._require("chat.context")
        self._assert_main_thread()
        if not callable(callback):
            raise PluginError("register_prompt_provider 的 callback 必须可调用。")
        self.manager.register_prompt_provider(self.info.id, callback)
        return callback

    def unregister_prompt_provider(self, handle: Callable[..., Any]) -> None:
        self._require("chat.context")
        self._assert_main_thread()
        self.manager.unregister_prompt_provider(self.info.id, handle)

    # ---------- 设置与存储 ----------

    @property
    def settings(self) -> dict[str, Any]:
        self._require("settings")
        return copy.deepcopy(self.manager.plugin_settings(self.info.id))

    def save_settings(self, values: dict[str, Any]) -> None:
        self._require("settings")
        if not isinstance(values, dict):
            raise PluginError("save_settings 需要字典。")
        self.manager.save_plugin_settings(self.info.id, values)

    @property
    def storage(self) -> dict[str, Any]:
        self._require("storage")
        if self._storage is None:
            self._storage = self.manager.load_storage(self.info.id)
        return self._storage

    def save_storage(self) -> None:
        self._require("storage")
        if self._storage is None:
            self._storage = {}
        self.manager.save_storage(self.info.id, self._storage)

    def log(self, message: str) -> None:
        self.manager.log(self.info.id, str(message))

    # ---------- 生命周期 ----------

    def shutdown(self) -> None:
        for timer in self._timers:
            timer.stop()
            timer.deleteLater()
        self._timers.clear()
        for request in list(self._requests.values()):
            request.cancel()
        self._requests.clear()
        self._callbacks.clear()
        for bridge in list(self._bridges.values()):
            bridge.deleteLater()
        self._bridges.clear()
        self._handlers.clear()
        try:
            self.manager.unregister_prompt_provider(self.info.id)
        except Exception:
            pass
        if self._model_api is not None:
            try:
                self.manager.pet.unload_live2d(self.info.id)
            except Exception:
                pass
            self._model_api = None
        try:
            self.manager.pet.clear_line_override(self.info.id)
        except Exception:
            pass


class PluginManager(QObject):
    changed = Signal()
    updates_checked = Signal(object, str)
    plugin_updated = Signal(str, str)
    marketplace_loaded = Signal(object, str)
    plugin_installed = Signal(str, str)
    plugin_install_progress = Signal(str, int, str)
    _downloaded = Signal(str, str, str)
    _market_downloaded = Signal(str, str, str)

    def __init__(
        self,
        config: Config,
        pet: Any,
        conversation: Any,
        parent: QObject | None = None,
        data_dir: Path | None = None,
        plugins_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.pet = pet
        self.conversation = conversation
        self.data_dir = Path(data_dir) if data_dir is not None else DATA_DIR
        self.plugins_dir = (
            Path(plugins_dir) if plugins_dir is not None else self.data_dir / "plugins"
        )
        self.state_dir = self.plugins_dir / ".state"
        self.log_path = self.data_dir / "plugins.log"
        self._infos: dict[str, PluginInfo] = {}
        self._loaded: dict[str, LoadedPlugin] = {}
        self._errors: dict[str, int] = {}
        self._ui_windows: dict[str, Any] = {}
        self._prompt_providers: dict[str, list[Callable[..., Any]]] = {}
        self._releases: dict[str, PluginRelease] = {}
        self._marketplace: dict[str, PluginRelease] = {}
        self._marketplace_loading = False
        self._installing = ""
        self._checking_updates = False
        self._update_queue: list[str] = []
        self._updating = False
        self._updating_plugin = ""
        self.updates_checked.connect(self._on_updates_checked)
        self._downloaded.connect(self._on_plugin_downloaded)
        self.plugin_updated.connect(self._on_plugin_update_finished)
        self.marketplace_loaded.connect(self._on_marketplace_loaded)
        self._market_downloaded.connect(self._on_market_downloaded)

    # ---------- 目录与发现 ----------

    def _ensure_dirs(self) -> None:
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        shipped = resource_dir() / "plugins"
        if shipped.is_dir():
            removed = self._removed_set()
            for child in shipped.iterdir():
                if not child.is_dir() or not (child / "plugin.json").exists():
                    continue
                if child.name in removed:
                    continue
                try:
                    if self._read_manifest(child).id in removed:
                        continue
                except PluginError:
                    pass
                target = self.plugins_dir / child.name
                if target.exists():
                    continue
                try:
                    shutil.copytree(child, target)
                except Exception as exc:
                    self.log("system", f"复制示例插件失败：{exc}")

    def _enabled_map(self) -> dict[str, bool]:
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            return {}
        enabled = plugins.get("enabled", {})
        return dict(enabled) if isinstance(enabled, dict) else {}

    def _save_enabled_map(self, values: dict[str, bool]) -> None:
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
        plugins["enabled"] = dict(values)
        self.config.set("plugins", plugins, save=True)

    def _removed_set(self) -> set[str]:
        """已被用户删除的内置插件，避免首次运行逻辑又把它复制回来。"""
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            return set()
        values = plugins.get("removed", [])
        if not isinstance(values, list):
            return set()
        return {str(item).strip() for item in values if str(item).strip()}

    def _save_removed_set(self, values: set[str]) -> None:
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
        plugins["removed"] = sorted(values)
        self.config.set("plugins", plugins, save=True)

    def _read_manifest(self, folder: Path) -> PluginInfo:
        manifest_path = folder / "plugin.json"
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PluginError("缺少 plugin.json") from exc
        except ValueError as exc:
            raise PluginError(f"plugin.json 不是合法 JSON：{exc}") from exc
        if not isinstance(raw, dict):
            raise PluginError("plugin.json 顶层必须是对象")

        plugin_id = str(raw.get("id", "")).strip()
        if not PLUGIN_ID_RE.match(plugin_id):
            raise PluginError("id 只能使用小写字母、数字、点、下划线、短横线，长度 2-64")
        try:
            api_version = int(raw.get("api_version", 1))
        except (TypeError, ValueError) as exc:
            raise PluginError("api_version 必须是整数") from exc
        if api_version != API_VERSION:
            raise PluginError(f"只支持 api_version={API_VERSION}，当前为 {api_version}")

        entry = str(raw.get("entry", "main.py")).strip() or "main.py"
        entry_path = (folder / entry).resolve()
        if folder.resolve() not in entry_path.parents and entry_path != folder.resolve():
            raise PluginError("entry 不能指向插件目录之外")
        if not entry_path.is_file():
            raise PluginError(f"找不到入口文件：{entry}")

        permissions_raw = raw.get("permissions", [])
        if not isinstance(permissions_raw, list):
            raise PluginError("permissions 必须是数组")
        permissions = [str(item) for item in permissions_raw if str(item).strip()]
        defaults = raw.get("settings", {})
        if not isinstance(defaults, dict):
            defaults = {}

        return PluginInfo(
            id=plugin_id,
            name=str(raw.get("name", plugin_id)).strip() or plugin_id,
            version=str(raw.get("version", "0.0.0")).strip() or "0.0.0",
            api_version=api_version,
            entry=entry,
            description=str(raw.get("description", "")).strip(),
            author=str(raw.get("author", "")).strip(),
            permissions=permissions,
            folder=folder,
            defaults=dict(defaults),
        )

    def discover(self) -> list[PluginInfo]:
        self._ensure_dirs()
        enabled = self._enabled_map()
        infos: dict[str, PluginInfo] = {}
        for child in sorted(self.plugins_dir.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / "plugin.json").exists():
                continue
            try:
                info = self._read_manifest(child)
            except PluginError as exc:
                info = PluginInfo(
                    id=f"invalid.{child.name}",
                    name=child.name,
                    version="?",
                    api_version=0,
                    entry="",
                    folder=child,
                    status="清单无效",
                    error=str(exc),
                )
            info.enabled = bool(enabled.get(info.id, False))
            if info.id in self._loaded:
                info.status = "运行中"
            elif info.status == "未启用" and info.enabled:
                info.status = "等待加载"
            infos[info.id] = info
        # 插件又出现在磁盘上（手动放回或从市场重装），删除标记就失效了。
        removed = self._removed_set()
        present = set()
        for info in infos.values():
            present.add(info.folder.name)
            if not info.id.startswith("invalid."):
                present.add(info.id)
        stale = removed & present
        if stale:
            self._save_removed_set(removed - stale)
        for info in infos.values():
            release = self._releases.get(info.id)
            info.latest_version = release.version if release is not None else ""
        self._infos = infos
        return self.plugins()

    def plugins(self) -> list[PluginInfo]:
        return sorted(self._infos.values(), key=lambda item: (item.name.lower(), item.id))

    def get(self, plugin_id: str) -> PluginInfo | None:
        return self._infos.get(plugin_id)

    # ---------- 加载与卸载 ----------

    def load_enabled(self) -> None:
        self.discover()
        for info in self.plugins():
            if info.enabled and info.id not in self._loaded and info.status != "清单无效":
                self.load_plugin(info.id)
        self.changed.emit()

    def load_plugin(self, plugin_id: str) -> bool:
        info = self._infos.get(plugin_id)
        if info is None:
            return False
        if plugin_id in self._loaded:
            return True
        if info.status == "清单无效":
            return False
        info.error = ""
        info.status = "加载中…"
        api: PluginAPI | None = None
        module_name = "_lfelkeea_plugin_" + re.sub(r"[^a-zA-Z0-9_]", "_", plugin_id)
        module = None
        old_path = list(sys.path)
        try:
            entry_path = info.folder / info.entry
            spec = importlib.util.spec_from_file_location(module_name, entry_path)
            if spec is None or spec.loader is None:
                raise PluginError("无法创建模块加载器")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            sys.path.insert(0, str(info.folder))
            importlib.invalidate_caches()
            spec.loader.exec_module(module)
            api = PluginAPI(self, info)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise PluginError("入口文件必须定义 setup(api) 函数")
            setup(api)
            self._loaded[plugin_id] = LoadedPlugin(info=info, module=module, api=api)
            info.status = "运行中"
            info.enabled = True
            api._dispatch("startup", {})
            self.log(plugin_id, "插件已加载")
            return True
        except Exception as exc:
            self.record_error(info, exc, "加载")
            info.status = "加载失败"
            info.error = self._short_error(exc)
            if api is not None:
                api.shutdown()
            if module_name in sys.modules:
                sys.modules.pop(module_name, None)
            return False
        finally:
            sys.path[:] = old_path
            self.changed.emit()

    def unload_plugin(self, plugin_id: str) -> None:
        loaded = self._loaded.pop(plugin_id, None)
        if loaded is None:
            return
        self._close_ui(plugin_id)
        try:
            loaded.api._dispatch("shutdown", {})
        except Exception:
            pass
        teardown = getattr(loaded.module, "teardown", None)
        if callable(teardown):
            try:
                teardown()
            except Exception as exc:
                self.record_error(loaded.info, exc, "teardown")
        loaded.api.shutdown()
        self._prompt_providers.pop(plugin_id, None)
        module_name = getattr(loaded.module, "__name__", "")
        if module_name in sys.modules:
            sys.modules.pop(module_name, None)
        loaded.info.status = "已停止" if loaded.info.enabled else "未启用"
        self.log(plugin_id, "插件已卸载")

    def reload_plugin(self, plugin_id: str) -> bool:
        info = self._infos.get(plugin_id)
        if info is None:
            return False
        was_enabled = bool(info.enabled)
        self.unload_plugin(plugin_id)
        self.discover()
        info = self._infos.get(plugin_id)
        if info is None:
            self.changed.emit()
            return False
        info.enabled = was_enabled
        ok = self.load_plugin(plugin_id) if was_enabled else True
        self.changed.emit()
        return ok

    def reload_all(self) -> None:
        for plugin_id in list(self._loaded):
            self.unload_plugin(plugin_id)
        self.load_enabled()

    def set_enabled(self, plugin_id: str, enabled: bool) -> bool:
        info = self._infos.get(plugin_id) or self.get(plugin_id)
        if info is None:
            return False
        values = self._enabled_map()
        values[plugin_id] = bool(enabled)
        self._save_enabled_map(values)
        info.enabled = bool(enabled)
        if enabled:
            ok = self.load_plugin(plugin_id)
        else:
            self.unload_plugin(plugin_id)
            info.status = "未启用"
            ok = True
        self.changed.emit()
        return ok

    # ---------- 删除插件 ----------

    def remove_plugin(self, plugin_id: str) -> tuple[bool, str]:
        """停止插件，把插件目录移入回收站，并清理启用状态、设置和存储。"""
        info = self._infos.get(plugin_id)
        if info is None:
            return False, "插件不存在，可能已经被移除。"
        if self._installing == plugin_id:
            return False, "插件正在安装，等安装完成后再删除。"
        if self._updating_plugin == plugin_id:
            return False, "插件正在更新，等更新完成后再删除。"

        if info.folder.is_symlink():
            return False, "拒绝删除：插件目录是符号链接。"
        folder = info.folder.resolve()
        root = self.plugins_dir.resolve()
        if folder == root or folder.parent != root:
            return False, "拒绝删除：插件目录必须是插件文件夹的直接子目录。"
        if not folder.is_dir():
            return False, "插件目录已经不存在。"

        name = info.name or info.id
        folder_name = folder.name
        manifest_id = "" if info.id.startswith("invalid.") else info.id
        keys = {folder_name}
        if manifest_id:
            keys.add(manifest_id)

        was_loaded = plugin_id in self._loaded
        was_enabled = bool(info.enabled)
        if was_loaded:
            self.unload_plugin(plugin_id)
        else:
            self._close_ui(plugin_id)

        ok, error = move_to_trash(folder)
        if not ok:
            # 删除失败时恢复刚才停掉的插件，避免“点了删除反而把插件停掉”。
            if was_loaded or was_enabled:
                self.discover()
                recovered = self._infos.get(plugin_id)
                if recovered is not None:
                    recovered.enabled = was_enabled
                    self.load_plugin(plugin_id)
            self.changed.emit()
            self.log(plugin_id or folder_name, f"删除失败：{error}")
            return False, f"没有删除：{error}"

        self._forget_plugin_config(keys)
        for key in keys:
            state_file = self._state_path(key)
            if state_file.is_file():
                moved, _ = move_to_trash(state_file)
                if not moved:
                    self.log(plugin_id or folder_name, f"清理状态文件失败：{state_file.name}")
        self._releases.pop(plugin_id, None)
        self._infos.pop(plugin_id, None)
        self.discover()
        self.changed.emit()
        self.log(plugin_id or folder_name, "插件已移入回收站")
        return True, f"已删除「{name}」，插件文件夹在回收站里，可从回收站恢复。"

    def _forget_plugin_config(self, plugin_ids: set[str]) -> None:
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
        enabled = plugins.get("enabled", {})
        enabled = dict(enabled) if isinstance(enabled, dict) else {}
        settings = plugins.get("settings", {})
        settings = dict(settings) if isinstance(settings, dict) else {}
        removed_raw = plugins.get("removed", [])
        removed = (
            {str(item).strip() for item in removed_raw if str(item).strip()}
            if isinstance(removed_raw, list)
            else set()
        )
        for key in plugin_ids:
            enabled.pop(key, None)
            settings.pop(key, None)
            removed.add(key)
        plugins["enabled"] = enabled
        plugins["settings"] = settings
        plugins["removed"] = sorted(removed)
        self.config.set("plugins", plugins, save=True)

    # ---------- 插件更新 ----------

    def registry_url(self) -> str:
        updates = self.config.get("updates", {})
        if isinstance(updates, dict):
            value = str(updates.get("plugin_registry", "") or "").strip()
            if value:
                return value
        return REGISTRY_URL

    def available_release(self, plugin_id: str) -> PluginRelease | None:
        return self._releases.get(plugin_id)

    def available_update_ids(self) -> list[str]:
        return sorted(self._releases)

    @property
    def checking_updates(self) -> bool:
        return self._checking_updates

    @property
    def updating(self) -> bool:
        return self._updating

    def check_plugin_updates(self) -> bool:
        if self._checking_updates:
            return False
        self._checking_updates = True
        installed = {
            info.id: info.version
            for info in self.plugins()
            if info.status != "清单无效"
        }
        url = self.registry_url()

        def worker() -> None:
            try:
                found = check_plugin_releases(installed, url=url)
            except Exception as exc:
                self.updates_checked.emit({}, str(exc))
                return
            self.updates_checked.emit(found, "")

        threading.Thread(
            target=worker, name="plugin-update-check", daemon=True
        ).start()
        return True

    def _on_updates_checked(self, releases: object, error: str) -> None:
        self._checking_updates = False
        if not error and isinstance(releases, dict):
            self._releases = dict(releases)
        else:
            self._releases = {}
        for info in self._infos.values():
            release = self._releases.get(info.id)
            info.latest_version = release.version if release is not None else ""
        if error:
            self.log("system", f"检查插件更新失败：{error}")
        self.changed.emit()

    def update_plugins(self, plugin_ids: list[str]) -> None:
        for plugin_id in plugin_ids:
            if plugin_id not in self._update_queue:
                self._update_queue.append(plugin_id)
        self._start_next_update()

    def update_plugin(self, plugin_id: str) -> bool:
        release = self._releases.get(plugin_id)
        if release is None:
            self.plugin_updated.emit(plugin_id, "没有可用的更新")
            return False
        self._updating = True
        self._updating_plugin = plugin_id

        def worker() -> None:
            try:
                archive = download_plugin(
                    release, self.data_dir / "updates" / "plugins"
                )
            except Exception as exc:
                self.plugin_updated.emit(plugin_id, str(exc))
                return
            self._downloaded.emit(plugin_id, str(archive), "")

        threading.Thread(
            target=worker, name="plugin-update-download", daemon=True
        ).start()
        return True

    def _start_next_update(self) -> None:
        if self._updating:
            return
        while self._update_queue:
            plugin_id = self._update_queue.pop(0)
            if plugin_id not in self._releases:
                continue
            if self.update_plugin(plugin_id):
                return

    def _on_plugin_downloaded(self, plugin_id: str, archive: str, error: str) -> None:
        if error:
            self.plugin_updated.emit(plugin_id, error)
            return
        info = self._infos.get(plugin_id)
        release = self._releases.get(plugin_id)
        if info is None or release is None:
            self.plugin_updated.emit(plugin_id, "插件已不存在")
            return
        enabled = bool(info.enabled)
        if plugin_id in self._loaded:
            self.unload_plugin(plugin_id)
        try:
            install_plugin(
                Path(archive),
                self.plugins_dir,
                plugin_id,
                release.version,
                backup_root=self.state_dir / "backups",
            )
        except Exception as exc:
            self.log(plugin_id, f"更新失败：{exc}")
            if enabled:
                self.load_plugin(plugin_id)
            self.plugin_updated.emit(plugin_id, str(exc))
            return
        self._releases.pop(plugin_id, None)
        self.discover()
        if enabled:
            self.load_plugin(plugin_id)
        self.log(plugin_id, f"已更新到 {release.version}")
        self.plugin_updated.emit(plugin_id, "")
        self.changed.emit()

    def _on_plugin_update_finished(self, plugin_id: str, error: str) -> None:
        self._updating = False
        self._updating_plugin = ""
        QTimer.singleShot(0, self._start_next_update)

    # ---------- 插件市场 ----------

    @property
    def loading_marketplace(self) -> bool:
        return self._marketplace_loading

    @property
    def installing_plugin(self) -> str:
        return self._installing

    def marketplace_releases(self) -> list[PluginRelease]:
        return sorted(
            self._marketplace.values(),
            key=lambda item: (not item.featured, item.bundled, item.name.lower(), item.id),
        )

    def marketplace_release(self, plugin_id: str) -> PluginRelease | None:
        return self._marketplace.get(plugin_id)

    def load_marketplace(self) -> bool:
        """在后台读取插件库清单，结果通过 marketplace_loaded 回到主线程。"""
        if self._marketplace_loading:
            return False
        self._marketplace_loading = True
        url = self.registry_url()

        def worker() -> None:
            try:
                data = fetch_registry(url)
                found = releases_from_registry(data, url)
            except Exception as exc:
                self.marketplace_loaded.emit({}, str(exc))
                return
            self.marketplace_loaded.emit(found, "")

        threading.Thread(
            target=worker, name="plugin-market-fetch", daemon=True
        ).start()
        return True

    def _on_marketplace_loaded(self, releases: object, error: str) -> None:
        self._marketplace_loading = False
        if not error and isinstance(releases, dict):
            self._marketplace = dict(releases)
        elif error:
            # 刷新失败时保留上一次的清单，避免市场突然变空。
            self.log("system", f"读取插件市场失败：{error}")
        self.changed.emit()

    def install_from_market(self, plugin_id: str) -> bool:
        if self._installing:
            return False
        release = self._marketplace.get(plugin_id)
        if release is None:
            self.plugin_installed.emit(plugin_id, "插件市场里没有这个插件")
            return False
        self._installing = plugin_id
        dest = self.data_dir / "updates" / "plugins"

        def worker() -> None:
            try:
                archive = download_plugin(
                    release,
                    dest,
                    progress_cb=lambda percent, message: self.plugin_install_progress.emit(
                        plugin_id, percent, message
                    ),
                )
            except Exception as exc:
                self._market_downloaded.emit(plugin_id, "", str(exc))
                return
            self._market_downloaded.emit(plugin_id, str(archive), "")

        threading.Thread(
            target=worker, name="plugin-market-download", daemon=True
        ).start()
        return True

    def _on_market_downloaded(self, plugin_id: str, archive: str, error: str) -> None:
        self._installing = ""
        if error:
            self.plugin_installed.emit(plugin_id, error)
            return
        release = self._marketplace.get(plugin_id)
        if release is None:
            self.plugin_installed.emit(plugin_id, "插件已从市场清单移除")
            return
        info = self._infos.get(plugin_id)
        enabled = bool(info and info.enabled)
        if plugin_id in self._loaded:
            self.unload_plugin(plugin_id)
        try:
            install_plugin(
                Path(archive),
                self.plugins_dir,
                plugin_id,
                release.version,
                backup_root=self.state_dir / "backups",
            )
        except Exception as exc:
            self.log(plugin_id, f"市场安装失败：{exc}")
            if enabled:
                self.load_plugin(plugin_id)
            self.plugin_installed.emit(plugin_id, str(exc))
            return
        self.discover()
        if enabled:
            self.load_plugin(plugin_id)
        self.log(plugin_id, f"已从插件市场安装 {release.version}")
        self.plugin_installed.emit(plugin_id, "")
        self.changed.emit()

    # ---------- 插件界面 ----------

    def has_ui(self, plugin_id: str) -> bool:
        loaded = self._loaded.get(plugin_id)
        if loaded is None:
            return False
        return callable(getattr(loaded.module, "open_settings", None))

    def open_ui(self, plugin_id: str, parent: Any = None) -> bool:
        """打开插件自带设置窗口。插件入口可选实现 open_settings(api, parent)。"""
        loaded = self._loaded.get(plugin_id)
        if loaded is None:
            return False
        opener = getattr(loaded.module, "open_settings", None)
        if not callable(opener):
            return False
        existing = self._ui_windows.get(plugin_id)
        if existing is not None:
            try:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                return True
            except Exception:
                self._close_ui(plugin_id)
        try:
            window = opener(loaded.api, parent)
        except Exception as exc:
            self.record_error(loaded.info, exc, "设置界面")
            return False
        if window is None:
            return False
        self._ui_windows[plugin_id] = window
        try:
            window.destroyed.connect(
                lambda *_, pid=plugin_id: self._ui_windows.pop(pid, None)
            )
            window.show()
            window.raise_()
            window.activateWindow()
        except Exception as exc:
            self.record_error(loaded.info, exc, "设置界面")
            self._ui_windows.pop(plugin_id, None)
            return False
        return True

    def _close_ui(self, plugin_id: str) -> None:
        window = self._ui_windows.pop(plugin_id, None)
        if window is None:
            return
        try:
            window.close()
            window.deleteLater()
        except Exception:
            pass

    # ---------- 事件 ----------

    def emit(self, event: str, **payload: Any) -> None:
        for loaded in list(self._loaded.values()):
            loaded.api._dispatch(event, payload)

    # ---------- 聊天上下文 ----------

    def register_prompt_provider(self, plugin_id: str, callback: Callable[..., Any]) -> None:
        # setup(api) 早于 _loaded 登记，这里不能要求插件已经出现在 _loaded 里。
        bucket = self._prompt_providers.setdefault(plugin_id, [])
        if callback not in bucket:
            bucket.append(callback)

    def unregister_prompt_provider(
        self, plugin_id: str, callback: Callable[..., Any] | None = None
    ) -> None:
        if callback is None:
            self._prompt_providers.pop(plugin_id, None)
            return
        bucket = self._prompt_providers.get(plugin_id)
        if not bucket:
            return
        self._prompt_providers[plugin_id] = [item for item in bucket if item is not callback]
        if not self._prompt_providers[plugin_id]:
            self._prompt_providers.pop(plugin_id, None)

    def prompt_context(
        self,
        query: str = "",
        viewer: str = "owner",
        source: str = "pet",
        channel: str = "private",
    ) -> str:
        """汇总所有插件的上下文，供聊天 / 语音 / 屏幕观察注入 system prompt。"""
        blocks: list[str] = []
        for plugin_id, callbacks in list(self._prompt_providers.items()):
            info = self._infos.get(plugin_id)
            if info is not None and plugin_id not in self._loaded:
                continue
            for callback in list(callbacks):
                try:
                    text = callback(
                        query=str(query or ""),
                        viewer=str(viewer or "owner"),
                        source=str(source or "pet"),
                        channel=str(channel or "private"),
                    )
                except Exception as exc:
                    if info is not None:
                        self.record_error(info, exc, "上下文")
                    continue
                text = str(text or "").strip()
                if text:
                    blocks.append(text)
        return "\n\n".join(blocks)

    # ---------- 设置 / 存储 ----------

    def plugin_settings(self, plugin_id: str) -> dict[str, Any]:
        info = self._infos.get(plugin_id)
        merged: dict[str, Any] = copy.deepcopy(info.defaults) if info is not None else {}
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            return merged
        settings = plugins.get("settings", {})
        if not isinstance(settings, dict):
            return merged
        value = settings.get(plugin_id, {})
        if isinstance(value, dict):
            merged.update(copy.deepcopy(value))
        return merged

    def save_plugin_settings(self, plugin_id: str, values: dict[str, Any]) -> None:
        plugins = self.config.get("plugins", {})
        if not isinstance(plugins, dict):
            plugins = {}
        settings = plugins.get("settings", {})
        if not isinstance(settings, dict):
            settings = {}
        merged = dict(settings.get(plugin_id, {}) or {})
        merged.update(values)
        settings[plugin_id] = merged
        plugins["settings"] = settings
        self.config.set("plugins", plugins, save=True)

    def _state_path(self, plugin_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", plugin_id)
        return self.state_dir / f"{safe}.json"

    def load_storage(self, plugin_id: str) -> dict[str, Any]:
        path = self._state_path(plugin_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_storage(self, plugin_id: str, values: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self._state_path(plugin_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ---------- 屏幕 ----------

    def capture_screen(self) -> ScreenContext:
        return capture_screen(max_edge=1280, quality=68)

    # ---------- 日志与错误 ----------

    def _short_error(self, exc: BaseException) -> str:
        text = f"{type(exc).__name__}: {exc}".strip()
        return text[:180]

    def record_error(self, info: PluginInfo, exc: BaseException, where: str) -> None:
        self._errors[info.id] = self._errors.get(info.id, 0) + 1
        info.error = self._short_error(exc)
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        self.log(info.id, f"{where}异常：{detail}")
        if self._errors[info.id] == 1:
            info.status = "运行出错（已隔离）"

    def log(self, plugin_id: str, message: str) -> None:
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > 512 * 1024:
                backup = self.log_path.with_suffix(".log.1")
                try:
                    if backup.exists():
                        backup.unlink()
                    self.log_path.replace(backup)
                except OSError:
                    pass
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"[{stamp}] [{plugin_id}] {message}\n"
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass

    def shutdown(self) -> None:
        for plugin_id in list(self._loaded):
            self.unload_plugin(plugin_id)
