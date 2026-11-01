from __future__ import annotations

"""QQ 桥接服务：白名单、人格、记忆、黑话、习惯、回复和 QQ 空间。"""

import json
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from . import analysis, tts as tts_engine, vision
from .commands import Command, help_text, parse_command
from .onebot import EventServer, OneBotClient, OneBotError, split_conversation
from .snowluma import SnowLumaManager
from .store import JsonStore
from .text import (
    build_segments,
    clean_incoming_text,
    image_sources,
    is_addressed,
    parse_event,
    parse_face_tags,
    parse_voice_tag,
    sanitize_reply,
    split_text,
)


LOG = logging.getLogger("lfelkeea.qq_bridge")
PLUGIN_ID = "lfelkeea.qq_bridge"

QZONE_RIGHTS = {"public": 1, "friends": 4, "some": 16, "private": 64, "hide": 128}
DEFAULT_PERSONA = "# 人格卡\n\n你是一只桌面小猫，说话简短、自然。"
# 这几个设置会被宿主用内存里的旧配置回写覆盖，额外在插件目录留一份。
LOCAL_SETTINGS_KEYS = ("auto_start_snowluma", "snowluma_dir", "vision_enabled")


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in ("1", "true", "yes", "on", "是", "开"):
        return True
    if text in ("0", "false", "no", "off", "否", "关"):
        return False
    return default


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_float(value: Any, default: float, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _as_ids(value: Any, digits_only: bool = True) -> list[str]:
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if digits_only:
            text = "".join(ch for ch in text if ch.isdigit())
        if text and text not in result:
            result.append(text)
    return result


def _clean_note(value: Any, limit: int = 40) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_notes(value: Any) -> dict[str, str]:
    """白名单备注：键统一成 private:123 / group:456，空备注直接丢掉。"""
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for raw_key, raw_note in value.items():
        kind, target = _parse_allow_target(str(raw_key or ""))
        note = _clean_note(raw_note)
        if kind == "invalid" or not target or not note:
            continue
        result[f"{kind}:{target}"] = note
        if len(result) >= 200:
            break
    return result


def _as_path_list(value: Any, limit: int = 50) -> list[str]:
    if isinstance(value, str):
        raw = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        text = str(item or "").strip().strip('"')
        if not text or text in result:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    permission = str(data.get("qzone_permission", "friends") or "friends").strip().lower()
    if permission not in QZONE_RIGHTS:
        permission = "friends"
    trigger = str(data.get("group_trigger", "at") or "at").strip().lower()
    if trigger not in ("at", "all"):
        trigger = "at"
    tts_engine_name = str(data.get("tts_engine", "windows") or "windows").strip().lower()
    if tts_engine_name not in ("windows", "gpt_sovits"):
        tts_engine_name = "windows"
    tts_langs = ("zh", "en", "ja", "ko", "yue", "auto")
    tts_prompt_lang = str(data.get("tts_prompt_lang", "zh") or "zh").strip().lower()
    if tts_prompt_lang not in tts_langs:
        tts_prompt_lang = "zh"
    tts_text_lang = str(data.get("tts_text_lang", "zh") or "zh").strip().lower()
    if tts_text_lang not in tts_langs:
        tts_text_lang = "zh"
    private_ids = _as_ids(data.get("allow_private"))
    group_ids = _as_ids(data.get("allow_groups"))
    allowed_keys = {f"private:{item}" for item in private_ids} | {
        f"group:{item}" for item in group_ids
    }
    notes = {
        key: note
        for key, note in _as_notes(data.get("allow_notes")).items()
        if key in allowed_keys
    }
    return {
        "snowluma_http": str(data.get("snowluma_http", "http://127.0.0.1:3000") or "").strip()
        or "http://127.0.0.1:3000",
        "access_token": str(data.get("access_token", "") or "").strip(),
        "http_port": _as_int(data.get("http_port"), 8766, 1, 65535),
        "http_host": str(data.get("http_host", "127.0.0.1") or "127.0.0.1").strip(),
        "event_path": str(data.get("event_path", "/onebot") or "/onebot").strip(),
        "event_secret": str(data.get("event_secret", "") or "").strip(),
        "owner_qq": "".join(ch for ch in str(data.get("owner_qq", "") or "") if ch.isdigit()),
        "allow_private": private_ids,
        "allow_groups": group_ids,
        "allow_notes": notes,
        "watch_users": _as_ids(data.get("watch_users")),
        "qzone_enabled": _as_bool(data.get("qzone_enabled"), False),
        "qzone_permission": permission,
        "qzone_target_uins": _as_ids(data.get("qzone_target_uins")),
        "qzone_auto_reply": _as_bool(data.get("qzone_auto_reply"), False),
        "qzone_reply_interval": _as_int(
            data.get("qzone_reply_interval"), 10, 1, 1440
        ),
        "qzone_reply_max": _as_int(data.get("qzone_reply_max"), 3, 1, 10),
        "group_trigger": trigger,
        "memory_enabled": _as_bool(data.get("memory_enabled"), True),
        "slang_enabled": _as_bool(data.get("slang_enabled"), True),
        "habit_enabled": _as_bool(data.get("habit_enabled"), True),
        "auto_face": _as_bool(data.get("auto_face"), True),
        "local_echo": _as_bool(data.get("local_echo"), False),
        "announce": _as_bool(data.get("announce"), False),
        "bot_name": str(data.get("bot_name", "小猫") or "小猫").strip()[:20],
        "command_prefix": str(data.get("command_prefix", "/") or "/").strip()[:3] or "/",
        "reply_chance": _as_float(data.get("reply_chance"), 0.15),
        "max_replies_per_minute": _as_int(data.get("max_replies_per_minute"), 6, 1, 60),
        "max_reply_chars": _as_int(data.get("max_reply_chars"), 300, 40, 1000),
        "face_ids": [item for item in _as_ids(data.get("face_ids")) if 0 <= int(item) <= 999][:8],
        "sticker_paths": [
            str(item).strip()
            for item in (data.get("sticker_paths") or [])
            if str(item or "").strip()
        ][:50]
        if isinstance(data.get("sticker_paths"), (list, tuple))
        else [],
        "face_probability": _as_float(data.get("face_probability"), 0.03, 0.0, 1.0),
        "sticker_probability": _as_float(data.get("sticker_probability"), 0.03, 0.0, 1.0),
        "memory_interval_minutes": _as_int(data.get("memory_interval_minutes"), 30, 5, 1440),
        "memory_min_batch": _as_int(data.get("memory_min_batch"), 8, 3, 100),
        "memory_batch_size": _as_int(data.get("memory_batch_size"), 40, 10, 200),
        "memory_max_items": _as_int(data.get("memory_max_items"), 80, 10, 500),
        "slang_max_terms": _as_int(data.get("slang_max_terms"), 200, 20, 1000),
        "auto_start_snowluma": _as_bool(data.get("auto_start_snowluma"), True),
        "snowluma_dir": str(data.get("snowluma_dir", "") or "").strip().strip('"'),
        "vision_enabled": _as_bool(data.get("vision_enabled"), True),
        "tts_engine": tts_engine_name,
        "tts_windows_voice": str(data.get("tts_windows_voice", "") or "").strip()[:120],
        "tts_rate": _as_int(data.get("tts_rate"), 0, -10, 10),
        "tts_volume": _as_int(data.get("tts_volume"), 100, 0, 100),
        "tts_gpt_sovits_url": str(
            data.get("tts_gpt_sovits_url", "http://127.0.0.1:9880")
            or "http://127.0.0.1:9880"
        ).strip(),
        "tts_ref_audio": str(data.get("tts_ref_audio", "") or "").strip().strip('"'),
        "tts_prompt_text": str(data.get("tts_prompt_text", "") or "").strip()[:300],
        "tts_prompt_lang": tts_prompt_lang,
        "tts_text_lang": tts_text_lang,
        "tts_speed": _as_float(data.get("tts_speed"), 1.0, 0.5, 2.0),
        "tts_reply_enabled": _as_bool(data.get("tts_reply_enabled"), False),
        "tts_reply_probability": _as_float(
            data.get("tts_reply_probability"), 0.08, 0.0, 1.0
        ),
        "tts_max_chars": _as_int(data.get("tts_max_chars"), 120, 20, 500),
        "custom_face_enabled": _as_bool(data.get("custom_face_enabled"), False),
        "custom_face_probability": _as_float(
            data.get("custom_face_probability"), 0.05, 0.0, 1.0
        ),
        "custom_face_ids": _as_ids(
            data.get("custom_face_ids"), digits_only=False
        )[:200],
    }


class QQBridgeService(QObject):
    event_received = Signal(dict)
    local_say = Signal(str, int)
    _dispatch = Signal(object)

    def __init__(self, api: Any) -> None:
        super().__init__()
        self.api = api
        self.persona_default = Path(api.info.folder) / "persona.md"
        self.state_root = Path(api.manager.state_dir) / PLUGIN_ID
        self.settings = self._initial_settings()
        self.store = JsonStore(self.state_root, self.persona_default)
        self.snowluma = SnowLumaManager(
            self.state_root, self._log, on_ready=self._after_snowluma_ready
        )
        self.client: OneBotClient | None = None
        self.server: EventServer | None = None
        self.started = False

        self._pending: dict[str, float] = {}
        self._compacting = False
        self._compact_started_at = 0.0
        self._compact_cooldown_until = 0.0
        self._compact_notify = ""
        self._reply_times: deque[float] = deque()
        self._vision_blocked = False
        self._checking = False
        self._last_check = 0.0
        self._custom_faces: list[dict[str, str]] = []
        self._custom_faces_loading = False
        self._qzone_checking = False
        self._qzone_queue: list[dict[str, Any]] = []
        self._qzone_left = 0

        self.event_received.connect(self._on_event_main)
        self.local_say.connect(self._say_main)
        self._dispatch.connect(self._run_dispatch)

    # ---------- 设置本地备份 ----------

    @property
    def _local_settings_path(self) -> Path:
        return self.state_root / "settings.json"

    def _load_local_settings(self) -> dict[str, Any]:
        try:
            data = json.loads(self._local_settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {key: data[key] for key in LOCAL_SETTINGS_KEYS if key in data}

    def _persist_local_settings(self, settings: dict[str, Any]) -> None:
        payload = {key: settings.get(key) for key in LOCAL_SETTINGS_KEYS}
        try:
            self.state_root.mkdir(parents=True, exist_ok=True)
            tmp = self._local_settings_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self._local_settings_path)
        except OSError as exc:
            self._log(f"保存本地设置备份失败：{exc}")

    def _initial_settings(self) -> dict[str, Any]:
        raw = dict(self.api.settings or {})
        local = self._load_local_settings()
        if local:
            settings = normalize_settings({**raw, **local})
            if any(settings.get(key) != raw.get(key) for key in LOCAL_SETTINGS_KEYS):
                try:
                    self.api.save_settings(settings)
                except Exception:
                    pass
            return settings
        # 旧版本没有本地备份，auto_start_snowluma 很可能是宿主用默认值回写的，
        # 这里先打开一次，之后用户关掉会写进本地备份。
        raw["auto_start_snowluma"] = True
        settings = normalize_settings(raw)
        self._persist_local_settings(settings)
        try:
            self.api.save_settings(settings)
        except Exception:
            pass
        return settings

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self.started:
            return
        self.settings = normalize_settings(self.api.settings)
        self.client = OneBotClient(
            self.settings["snowluma_http"], self.settings["access_token"]
        )
        self.server = EventServer(
            self.settings["http_host"],
            self.settings["http_port"],
            self.settings["event_path"],
            self.settings["event_secret"],
            on_event=self._on_event_thread,
            on_error=self._log,
        )
        try:
            port = self.server.start()
            self._log(f"事件接收已启动：http://{self.settings['http_host']}:{port}{self.settings['event_path']}")
            self.api.say(
                f"QQ 桥接已启动，监听端口 {port}。在插件管理里点“设置”填写 SnowLuma 和白名单。",
                5000,
            )
        except Exception as exc:
            self._log(f"事件端口启动失败：{exc}")
            self.server = None
            self.api.say("QQ 桥接的事件端口被占用，请在设置里换一个 http_port。", 6000)

        self.api.every(60_000, self._tick)
        self.started = True
        if self.settings["announce"]:
            self.api.say("QQ 桥接上线了。", 4000)
        self._log("QQ 桥接已加载")
        self.snowluma.configure(self.settings, self.settings["snowluma_http"])
        self.snowluma.start_async()
        self.check_async()
        if self.settings["custom_face_enabled"]:
            self.refresh_custom_faces_async()

    def stop(self) -> None:
        self.snowluma.stop()
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.store.flush()
        self._pending.clear()
        self._qzone_queue.clear()
        self._qzone_checking = False
        self.started = False
        self._log("QQ 桥接已停止")

    def _tick(self) -> None:
        if not self.started:
            return
        try:
            self.store.flush()
        except Exception as exc:
            self._log(f"落盘失败：{exc}")

        now = time.time()
        for conversation, started_at in list(self._pending.items()):
            if now - started_at > 180:
                self._pending.pop(conversation, None)
                self._log(f"{conversation} 的 AI 回复超时，已解除占用。")

        if self._compacting and now - self._compact_started_at > 300:
            self._compacting = False
            self._log("记忆整理超时，已重置。")
        if not self._checking and now - self._last_check >= 300:
            self.check_async()
        self._maybe_compact()
        self._maybe_qzone_auto_reply()

    # ---------- 线程投递 ----------

    def _on_event_thread(self, payload: dict[str, Any]) -> None:
        self.event_received.emit(payload)

    def _run_dispatch(self, callback: Any) -> None:
        try:
            callback()
        except Exception as exc:
            self._log(f"后台回调出错：{exc}")

    def _after_snowluma_ready(self) -> None:
        if not self.started:
            return
        self._dispatch.emit(self.check_async)

    def snowluma_status(self) -> dict[str, Any]:
        return self.snowluma.status()

    def takeover_snowluma(self, callback: Callable[[str], None]) -> None:
        """结束旧的 SnowLuma 并交给插件后台启动（可能耗时，放到线程里）。"""

        def work() -> None:
            message = self.snowluma.takeover()
            self._dispatch.emit(lambda: callback(message))

        threading.Thread(target=work, name="qq-bridge-takeover", daemon=True).start()

    def _log(self, message: str) -> None:
        try:
            self.api.log(str(message))
        except Exception:
            LOG.info(message)

    def _say_main(self, text: str, duration: int = 4000) -> None:
        try:
            self.api.say(text, duration)
        except Exception as exc:
            self._log(f"桌宠说话失败：{exc}")

    # ---------- 设置与白名单 ----------

    @property
    def owner(self) -> str:
        return self.settings["owner_qq"]

    def is_owner(self, user_id: str) -> bool:
        return bool(self.owner) and str(user_id) == self.owner

    def is_watched(self, user_id: str) -> bool:
        value = str(user_id or "").strip()
        return bool(value) and value in self.settings["watch_users"]

    def is_allowed(self, conversation: str, user_id: str = "") -> bool:
        kind, target = split_conversation(conversation)
        if kind == "group":
            return target in self.settings["allow_groups"]
        # 盯人名单里的账号可以直接私聊，不需要先加白名单。
        if self.is_watched(target) or self.is_watched(user_id):
            return True
        if self.is_owner(target) or self.is_owner(user_id):
            return True
        return target in self.settings["allow_private"]

    def _save_whitelist(self) -> None:
        try:
            self.api.save_settings(
                {
                    "allow_private": list(self.settings["allow_private"]),
                    "allow_groups": list(self.settings["allow_groups"]),
                    "allow_notes": dict(self.settings["allow_notes"]),
                }
            )
        except Exception as exc:
            self._log(f"保存白名单失败：{exc}")

    def _save_watch(self) -> None:
        try:
            self.api.save_settings({"watch_users": list(self.settings["watch_users"])})
        except Exception as exc:
            self._log(f"保存盯人名单失败：{exc}")

    def watch_items(self) -> list[str]:
        return list(self.settings["watch_users"])

    def watch_add(self, target: str) -> str:
        value = "".join(ch for ch in str(target or "") if ch.isdigit())
        if not value:
            return "请填写要盯的 QQ 号（纯数字）。"
        if value in self.settings["watch_users"]:
            return "这个 QQ 号已经在盯人名单里了。"
        self.settings["watch_users"].append(value)
        self._save_watch()
        return f"已开始盯 {value}。"

    def watch_remove(self, target: str) -> str:
        value = "".join(ch for ch in str(target or "") if ch.isdigit())
        if not value or value not in self.settings["watch_users"]:
            return "盯人名单里没有这个号。"
        self.settings["watch_users"].remove(value)
        self._save_watch()
        return f"已取消盯 {value}。"

    def _allow_target(
        self, kind: str, target: str, add: bool, note: str | None = None
    ) -> str:
        bucket = "allow_groups" if kind == "group" else "allow_private"
        values = list(self.settings[bucket])
        notes = dict(self.settings["allow_notes"])
        key = f"{kind}:{target}"
        cleaned = _clean_note(note) if note is not None else ""
        if add:
            if target in values:
                if not cleaned:
                    return "已经在白名单里了。"
                if notes.get(key) == cleaned:
                    return "备注没有变化。"
                notes[key] = cleaned
                self.settings["allow_notes"] = notes
                self._save_whitelist()
                return f"已更新备注：{key}（{cleaned}）"
            values.append(target)
            if cleaned:
                notes[key] = cleaned
            else:
                notes.pop(key, None)
            self.settings[bucket] = values
            self.settings["allow_notes"] = notes
            self._save_whitelist()
            suffix = f"（{cleaned}）" if cleaned else ""
            return f"已加入白名单：{key}{suffix}"
        if target not in values:
            return "白名单里没有这个号。"
        values.remove(target)
        notes.pop(key, None)
        self.settings[bucket] = values
        self.settings["allow_notes"] = notes
        self._save_whitelist()
        return f"已移出白名单：{key}"

    def apply_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        """保存设置；连接相关字段变化时重建客户端和事件监听。"""
        merged = dict(self.settings)
        for key, value in (values or {}).items():
            if key in merged:
                merged[key] = value
        updated = normalize_settings(merged)
        try:
            self.api.save_settings(updated)
        except Exception as exc:
            self._log(f"保存设置失败：{exc}")
            return {"ok": False, "message": f"保存失败：{exc}", "settings": self.settings}
        self._persist_local_settings(updated)
        link_keys = (
            "snowluma_http",
            "access_token",
            "http_host",
            "http_port",
            "event_path",
            "event_secret",
        )
        changed = any(self.settings.get(key) != updated.get(key) for key in link_keys)
        dir_changed = self.settings.get("snowluma_dir") != updated.get("snowluma_dir")
        was_qzone_auto = bool(self.settings.get("qzone_auto_reply"))
        self.settings = updated
        if updated["qzone_auto_reply"] and not was_qzone_auto:
            # 新开启时把当前已有评论当作基线，不补回复旧评论。
            self.store.reset_qzone_baseline()
            self.store.mark_qzone_check(0)
            self.store.flush()
        if updated["custom_face_enabled"] and not self._custom_faces:
            self.refresh_custom_faces_async()
        self.snowluma.configure(updated, updated["snowluma_http"])
        if changed and self.started:
            message = self._rebuild_link()
        else:
            message = "设置已保存。"
        if not updated["auto_start_snowluma"]:
            self.snowluma.stop()
        else:
            if dir_changed:
                self.snowluma.stop()
            self.snowluma.start_async()
        return {"ok": True, "message": message, "settings": updated}

    def _rebuild_link(self) -> str:
        if self.server is not None:
            self.server.stop()
            self.server = None
        self.client = OneBotClient(
            self.settings["snowluma_http"], self.settings["access_token"]
        )
        self.server = EventServer(
            self.settings["http_host"],
            self.settings["http_port"],
            self.settings["event_path"],
            self.settings["event_secret"],
            on_event=self._on_event_thread,
            on_error=self._log,
        )
        try:
            port = self.server.start()
            message = f"已重新监听事件端口 {port}。"
        except Exception as exc:
            self.server = None
            message = f"事件端口启动失败：{exc}"
        self._log(message)
        self.check_async(manual=True)
        return message

    # ---------- 设置界面数据 ----------

    def snapshot(self) -> dict[str, Any]:
        connection = self.store.runtime.get("connection_ok")
        port = self.server.bound_port if self.server is not None else 0
        return {
            "started": self.started,
            "connection_ok": connection,
            "http": self.settings["snowluma_http"],
            "event_port": port,
            "event_url": (
                f"http://{self.settings['http_host']}:"
                f"{port or self.settings['http_port']}{self.settings['event_path']}"
            ),
            "owner_qq": self.settings["owner_qq"],
            "private_count": len(self.settings["allow_private"]),
            "group_count": len(self.settings["allow_groups"]),
            "memory_count": len(self.store.memories(500)),
            "slang_count": len(self.store.slang_terms()),
            "watch_count": len(self.settings["watch_users"]),
            "qzone_enabled": self.settings["qzone_enabled"],
            "qzone_auto_reply": bool(self.settings["qzone_auto_reply"]),
            "qzone_replied_count": self.store.qzone_replied_count(),
            "qzone_last_check": float(
                self.store.runtime.get("qzone_last_check", 0) or 0
            ),
            "auto_start_snowluma": bool(self.settings["auto_start_snowluma"]),
            "snowluma_dir": self.settings["snowluma_dir"],
            "vision_enabled": bool(self.settings["vision_enabled"]),
            "tts_engine": self.settings["tts_engine"],
            "tts_configured": self._tts_ready(),
            "tts_reply_enabled": bool(self.settings["tts_reply_enabled"]),
            "custom_face_enabled": bool(self.settings["custom_face_enabled"]),
            "custom_face_count": len(self._custom_faces)
            or len(self.settings["custom_face_ids"]),
            "snowluma_managed": self.snowluma.status(),
        }

    def probe(self, callback: Callable[[bool, str], None]) -> None:
        client = self.client
        if client is None:
            callback(False, "OneBot 客户端还没准备好。")
            return

        def work() -> None:
            try:
                info = client.get_login_info()
                user_id = str(info.get("user_id", "") or "")
                nickname = str(info.get("nickname", "") or "")
                text = f"已连接：{nickname or 'QQ'}（{user_id}）"
                ok = True
            except Exception as exc:
                text = f"连接失败：{exc}"
                ok = False
            self._dispatch.emit(lambda: self._finish_probe(ok, text, callback))

        threading.Thread(target=work, name="qq-bridge-probe", daemon=True).start()

    def _finish_probe(
        self, ok: bool, text: str, callback: Callable[[bool, str], None]
    ) -> None:
        self.store.set_connection(ok)
        self.store.flush()
        self._log(text)
        try:
            callback(ok, text)
        except Exception as exc:
            self._log(f"连接检查回调失败：{exc}")

    def send_test(
        self, conversation: str, text: str, callback: Callable[[bool, str], None]
    ) -> None:
        kind, target = split_conversation(conversation)
        if kind == "invalid" or not target:
            callback(False, "目标格式不对，用 private:QQ号 或 group:群号。")
            return
        target_conversation = f"{kind}:{target}"
        if not self.is_allowed(target_conversation):
            callback(False, "这个对象不在白名单里，先在“白名单”页添加。")
            return
        cleaned, faces = parse_face_tags(str(text or ""))
        segments = build_segments(cleaned, faces)
        if not segments:
            callback(False, "消息内容为空。")
            return
        client = self.client
        if client is None:
            callback(False, "OneBot 客户端还没准备好。")
            return

        def work() -> None:
            try:
                client.send(target_conversation, segments)
                result = (True, f"已发送到 {target_conversation}。")
            except Exception as exc:
                result = (False, f"发送失败：{exc}")
            ok, message = result
            self._dispatch.emit(lambda: callback(ok, message))

        threading.Thread(target=work, name="qq-bridge-test-send", daemon=True).start()

    def qzone_action(
        self,
        action: str,
        value: str = "",
        callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        client = self.client
        if client is None:
            if callback is not None:
                callback(False, "OneBot 客户端还没准备好。")
            return
        if action == "publish" and not self.settings["qzone_enabled"]:
            if callback is not None:
                callback(False, "先在本页打开“允许发 QQ 空间”。")
            return

        def work() -> None:
            try:
                if action == "publish":
                    client.publish_qzone(
                        value,
                        QZONE_RIGHTS[self.settings["qzone_permission"]],
                        target_uins=self.settings["qzone_target_uins"],
                    )
                    result = (True, "已发到 QQ 空间。")
                elif action == "delete":
                    client.delete_qzone(value)
                    result = (True, "那条说说已删除。")
                else:
                    result = (True, _format_qzone(client.list_qzone(10)))
            except Exception as exc:
                result = (False, f"QQ 空间操作失败：{exc}")
            ok, message = result
            if callback is not None:
                self._dispatch.emit(lambda: callback(ok, message))

        threading.Thread(target=work, name="qq-bridge-qzone-ui", daemon=True).start()

    def persona_text(self) -> str:
        return self.store.persona()

    def set_persona_text(self, text: str) -> str:
        length = self.store.set_persona(text)
        self.store.flush()
        return f"人格卡已保存（{length} 字）。"

    def reset_persona(self) -> str:
        text = DEFAULT_PERSONA
        try:
            text = self.persona_default.read_text(encoding="utf-8").strip() or text
        except OSError:
            pass
        self.store.set_persona(text)
        self.store.flush()
        return "人格卡已恢复默认。"

    def memory_items(self, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.memories(limit)

    def add_memory_text(self, text: str) -> str:
        item = self.store.add_memory(text, importance=60, source="manual")
        self.store.prune_memory(self.settings["memory_max_items"])
        self.store.flush()
        return "记住了。" if item else "这条内容为空。"

    def remove_memory_text(self, keyword: str) -> str:
        removed = self.store.remove_memory(keyword)
        self.store.flush()
        return f"删除了 {removed} 条记忆。"

    def compact_now(self, callback: Callable[[str], None]) -> None:
        self._maybe_compact(force=True, callback=callback)

    def slang_items(self) -> list[dict[str, Any]]:
        terms = self.store.slang_terms()
        return sorted(
            (dict(item) for item in terms.values() if isinstance(item, dict)),
            key=lambda item: int(item.get("count", 0)),
            reverse=True,
        )

    def add_slang_text(self, term: str, meaning: str) -> str:
        entry = self.store.add_slang(term, meaning, source="manual")
        self.store.prune_slang(self.settings["slang_max_terms"])
        self.store.flush()
        return "收进黑话库了。" if entry else "词或含义为空，没收录。"

    def remove_slang_text(self, term: str) -> str:
        removed = self.store.remove_slang(term)
        self.store.flush()
        return "删掉了。" if removed else "黑话库里没有这个词。"

    def allow_items(self) -> dict[str, list[str]]:
        return {
            "private": list(self.settings["allow_private"]),
            "group": list(self.settings["allow_groups"]),
        }

    def allow_notes(self) -> dict[str, str]:
        return dict(self.settings["allow_notes"])

    def allow_note(self, kind: str, target: str) -> str:
        key = f"{'group' if kind == 'group' else 'private'}:{str(target or '').strip()}"
        return str(self.settings["allow_notes"].get(key, ""))

    def set_allow_note(self, kind: str, target: str, note: str) -> str:
        bucket = "allow_groups" if kind == "group" else "allow_private"
        value = "".join(ch for ch in str(target or "") if ch.isdigit())
        if not value:
            return "请先选中白名单里的好友或群。"
        if value not in self.settings[bucket]:
            return "白名单里没有这个号。"
        key = f"{'group' if kind == 'group' else 'private'}:{value}"
        notes = dict(self.settings["allow_notes"])
        cleaned = _clean_note(note)
        if cleaned:
            if notes.get(key) == cleaned:
                return "备注没有变化。"
            notes[key] = cleaned
            message = f"备注已保存：{key}（{cleaned}）"
        else:
            if key not in notes:
                return "这个号还没有备注。"
            notes.pop(key, None)
            message = f"备注已清空：{key}"
        self.settings["allow_notes"] = notes
        self._save_whitelist()
        return message

    def allow_target_text(
        self, kind: str, target: str, add: bool, note: str | None = None
    ) -> str:
        digits = "".join(ch for ch in str(target or "") if ch.isdigit())
        if not digits:
            return "请填写 QQ 号或群号（纯数字）。"
        return self._allow_target(
            "group" if kind == "group" else "private", digits, add, note
        )

    # ---------- TTS 语音 ----------

    def _tts_ready(self) -> bool:
        engine = self.settings["tts_engine"]
        if engine == "windows":
            return True
        if engine == "gpt_sovits":
            return bool(
                self.settings["tts_gpt_sovits_url"]
                and self.settings["tts_ref_audio"]
            )
        return False

    def tts_voices(self) -> list[str]:
        """列出 Windows 已安装语音；失败时返回空列表。"""
        try:
            return tts_engine.windows_voices()
        except Exception as exc:
            self._log(f"读取 Windows 语音失败：{exc}")
            return []

    def _tts_output_dir(self) -> Path:
        path = self.state_root / "tts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _synthesize_tts(self, text: str) -> Path:
        if not self._tts_ready():
            raise tts_engine.TTSError("TTS 还没有配置好，先到「语音」页选择引擎。")
        value = tts_engine.clean_text(text, self.settings["tts_max_chars"])
        if not value:
            raise tts_engine.TTSError("没有可合成的文字。")
        stamp = f"{int(time.time() * 1000)}-{random.randint(100, 999)}"
        out = self._tts_output_dir() / f"tts-{stamp}.wav"
        if self.settings["tts_engine"] == "gpt_sovits":
            path = tts_engine.synthesize_gpt_sovits(
                value,
                out,
                base_url=self.settings["tts_gpt_sovits_url"],
                ref_audio=self.settings["tts_ref_audio"],
                prompt_text=self.settings["tts_prompt_text"],
                prompt_lang=self.settings["tts_prompt_lang"],
                text_lang=self.settings["tts_text_lang"],
                speed=self.settings["tts_speed"],
            )
        else:
            path = tts_engine.synthesize_windows(
                value,
                out,
                voice=self.settings["tts_windows_voice"],
                rate=self.settings["tts_rate"],
                volume=self.settings["tts_volume"],
            )
        self._cleanup_tts_files()
        return path

    def _cleanup_tts_files(self, keep: int = 20) -> None:
        try:
            files = sorted(
                self._tts_output_dir().glob("tts-*.wav"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        for path in files[max(1, int(keep)):]:
            try:
                path.unlink()
            except OSError:
                continue

    def _should_tts(self, explicit: bool) -> bool:
        if not self._tts_ready():
            return False
        if explicit:
            return True
        if not self.settings["tts_reply_enabled"]:
            return False
        return random.random() < self.settings["tts_reply_probability"]

    def preview_tts(
        self,
        text: str,
        callback: Callable[[str, str], None] | None = None,
    ) -> None:
        """合成一条试听语音，结果回主线程：(文件路径, 错误)。"""

        def work() -> None:
            try:
                path = str(self._synthesize_tts(text))
                error = ""
            except Exception as exc:
                path, error = "", str(exc)
                self._log(f"TTS 试听失败：{exc}")
            if callback is not None:
                self._dispatch.emit(lambda: callback(path, error))

        threading.Thread(target=work, name="qq-bridge-tts-preview", daemon=True).start()

    def send_tts(
        self,
        conversation: str,
        text: str,
        callback: Callable[[bool, str], None] | None = None,
    ) -> None:
        kind, target = split_conversation(conversation)
        if kind == "invalid" or not target:
            if callback is not None:
                callback(False, "目标格式不对，用 private:QQ号 或 group:群号。")
            return
        target_conversation = f"{kind}:{target}"
        if not self.is_allowed(target_conversation):
            if callback is not None:
                callback(False, "这个对象不在白名单里，先在“白名单”页添加。")
            return
        if not tts_engine.clean_text(text, self.settings["tts_max_chars"]):
            if callback is not None:
                callback(False, "要合成的文字为空。")
            return
        client = self.client
        if client is None:
            if callback is not None:
                callback(False, "OneBot 客户端还没准备好。")
            return

        def work() -> None:
            try:
                path = self._synthesize_tts(text)
                client.send(
                    target_conversation,
                    [{"type": "record", "data": {"file": str(path)}}],
                )
                result = (True, f"已发送 TTS 语音到 {target_conversation}。")
            except Exception as exc:
                self._log(f"TTS 语音发送失败：{exc}")
                result = (False, f"TTS 语音发送失败：{exc}")
            if callback is not None:
                ok, message = result
                self._dispatch.emit(lambda: callback(ok, message))

        threading.Thread(target=work, name="qq-bridge-tts-send", daemon=True).start()

    def _speak_reply(self, conversation: str, text: str) -> None:
        client = self.client
        if client is None:
            return

        def work() -> None:
            try:
                path = self._synthesize_tts(text)
                client.send(
                    conversation,
                    [{"type": "record", "data": {"file": str(path)}}],
                )
                self._log("已随回复发送 TTS 语音。")
            except Exception as exc:
                self._log(f"回复附带 TTS 语音失败：{exc}")

        threading.Thread(target=work, name="qq-bridge-tts-reply", daemon=True).start()

    def habit_rows(self, limit: int = 60) -> list[dict[str, Any]]:
        conversations = self.store.habits.get("conversations", {})
        if not isinstance(conversations, dict):
            return []
        rows: list[dict[str, Any]] = []
        for conversation, data in conversations.items():
            if not isinstance(data, dict):
                continue
            count = max(1, int(data.get("count", 0) or 1))
            rows.append(
                {
                    "conversation": str(conversation),
                    "count": int(data.get("count", 0)),
                    "short_rate": int(data.get("short", 0)) / count,
                    "question": int(data.get("question", 0)),
                    "faces": int(data.get("faces", 0)),
                    "updated_at": float(data.get("updated_at", 0) or 0),
                }
            )
        rows.sort(key=lambda item: item["updated_at"], reverse=True)
        return rows[: max(1, int(limit))]

    def remove_habit(self, conversation: str) -> str:
        key = str(conversation or "").strip()
        if not key:
            return "会话为空，没有删除。"
        if not self.store.remove_habit(key):
            return "没找到这个会话的习惯数据。"
        self.store.flush()
        return f"已删除 {key} 的聊天习惯。"

    def log_tail(self, limit: int = 40) -> str:
        path = Path(getattr(self.api.manager, "log_path", ""))
        try:
            if path.is_file() and path.stat().st_size < 2 * 1024 * 1024:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                return "\n".join(lines[-max(1, int(limit)) :])
        except OSError:
            pass
        return ""

    # ---------- 事件入口 ----------

    def _on_event_main(self, payload: dict[str, Any]) -> None:
        if not self.started:
            return
        try:
            message = parse_event(payload)
        except Exception as exc:
            self._log(f"消息解析失败：{exc}")
            return
        if message is None:
            return
        if message.message_id and self.store.seen(message.message_id):
            return
        if message.message_id:
            self.store.mark_seen(message.message_id)
        if not self.is_allowed(message.conversation, message.user_id):
            return

        text = clean_incoming_text(message.text, self.settings["bot_name"])
        if self.settings["habit_enabled"]:
            self.store.observe(message.conversation, text, message.face_count)
        if text:
            self.store.append_recent(
                message.conversation, "user", text, name=message.sender_name
            )
        self.store.append_inbox(
            message.conversation,
            "user",
            text or "[图片]",
            name=message.sender_name,
        )

        if self.is_owner(message.user_id) and (
            message.is_private or message.group_id in self.settings["allow_groups"]
        ):
            command = parse_command(text, self.settings["command_prefix"])
            if command is not None:
                self._handle_command(command, message)
                return

        watch = self._is_watch_hit(message)
        if not watch and not is_addressed(
            message,
            self.settings["bot_name"],
            self.settings["group_trigger"],
            self.settings["reply_chance"],
        ):
            return
        if not text and not message.has_image:
            return
        self._reply_with_ai(message, text, watch=watch)

    def _is_watch_hit(self, message: Any) -> bool:
        """盯人名单命中：私聊直接回；群里只认白名单群。"""
        if not self.is_watched(message.user_id):
            return False
        if message.is_private:
            return True
        return message.group_id in self.settings["allow_groups"]

    # ---------- AI 回复 ----------

    def _rate_ok(self) -> bool:
        now = time.time()
        while self._reply_times and now - self._reply_times[0] > 60:
            self._reply_times.popleft()
        if len(self._reply_times) >= self.settings["max_replies_per_minute"]:
            return False
        self._reply_times.append(now)
        return True

    def _reply_with_ai(self, message: Any, text: str, watch: bool = False) -> None:
        conversation = message.conversation
        if conversation in self._pending:
            return
        if not watch and not self._rate_ok():
            self._log("触发每分钟回复上限，本条跳过。")
            return

        memory = (
            analysis.memory_prompt(self.store.memories(8))
            if self.settings["memory_enabled"]
            else ""
        )
        slang = (
            analysis.slang_prompt(self.store.slang_terms(), text)
            if self.settings["slang_enabled"]
            else ""
        )
        habit = analysis.habit_summary(self.store.habit(conversation))
        system_prompt = analysis.build_system_prompt(
            self.store.persona(), memory, slang, habit
        )
        if self._tts_ready():
            system_prompt += (
                "\n\n你还可以发 QQ 语音：如果这句话适合用语音说，"
                "在回复末尾加 [voice]，系统会用 TTS 把你这句话读成语音一起发出去。"
                "不想发语音就不要加这个标记，也不要向对方解释这个标记。"
            )
        recent = self.store.recent(conversation, 9)
        if recent and recent[-1].get("text") == (text or "[图片]"):
            recent = recent[:-1]
        user_prompt = analysis.build_user_prompt(
            recent,
            text or "[图片]",
            sender=message.sender_name,
        )

        self._pending[conversation] = time.time()

        def on_reply(reply: str | None, error: str | None) -> None:
            self._pending.pop(conversation, None)
            if error:
                self._log(f"AI 回复失败：{error}")
                return
            cleaned = sanitize_reply(reply or "", self.settings["max_reply_chars"])
            if not cleaned:
                return
            cleaned, wants_voice = parse_voice_tag(cleaned)
            should_speak = self._should_tts(wants_voice)
            faces, images = self._pick_extras(conversation, cleaned)
            cleaned, explicit = parse_face_tags(cleaned)
            if explicit:
                faces = explicit
            cleaned = clean_incoming_text(cleaned, limit=1000)
            should_speak = should_speak and bool(cleaned)
            if not cleaned and not images and not should_speak:
                return
            segments = build_segments(cleaned, faces, images)
            if segments:
                self._send_segments(conversation, segments, cleaned or "[表情]")
            if should_speak:
                self._speak_reply(conversation, cleaned)
            custom_face = self._pick_custom_face()
            if custom_face:
                self._send_custom_face(conversation, custom_face)
            self.store.append_recent(conversation, "assistant", cleaned)
            self.store.append_inbox(
                conversation, "assistant", cleaned, name=self.settings["bot_name"]
            )

        sources = image_sources(message) if self.settings["vision_enabled"] else []
        if not sources:
            self._start_chat(
                conversation, system_prompt, user_prompt, [], "", on_reply
            )
            return

        blocked = self._vision_blocked

        def prepare() -> None:
            prepared = vision.prepare(sources, run_ocr_when_empty=blocked)
            if blocked:
                prepared.uris = []
                if prepared.blobs and not prepared.ocr_text:
                    prepared.ocr_text = vision.ocr_images(prepared.blobs)
            self._dispatch.emit(
                lambda: self._start_chat(
                    conversation,
                    system_prompt,
                    user_prompt,
                    prepared.uris,
                    prepared.ocr_text,
                    on_reply,
                    prepared,
                )
            )

        threading.Thread(target=prepare, name="qq-bridge-vision", daemon=True).start()

    def _start_chat(
        self,
        conversation: str,
        system_prompt: str,
        user_prompt: str,
        uris: list[str],
        ocr_text: str,
        on_reply: Callable[[str | None, str | None], None],
        prepared: vision.PreparedImages | None = None,
    ) -> None:
        if conversation not in self._pending:
            return
        prompt = user_prompt
        if ocr_text:
            prompt = f"{user_prompt}\n\n（图片里的文字，供参考：{ocr_text}）"
        content: Any = prompt
        if uris and not self._vision_blocked:
            content = [{"type": "text", "text": prompt}]
            content.extend(
                {"type": "image_url", "image_url": {"url": uri}} for uri in uris
            )

        def guarded(reply: str | None, error: str | None) -> None:
            if (
                error
                and uris
                and not self._vision_blocked
                and vision.looks_like_vision_rejection(error)
            ):
                self._vision_blocked = True
                self._log("当前 AI 接口不接受图片输入，改按文字或 OCR 结果回复。")
                self._retry_without_images(
                    conversation, system_prompt, prompt, prepared, on_reply
                )
                return
            on_reply(reply, error)

        try:
            self.api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                guarded,
            )
        except Exception as exc:
            self._pending.pop(conversation, None)
            self._log(f"发起 AI 请求失败：{exc}")

    def _retry_without_images(
        self,
        conversation: str,
        system_prompt: str,
        user_prompt: str,
        prepared: vision.PreparedImages | None,
        on_reply: Callable[[str | None, str | None], None],
    ) -> None:
        def work() -> None:
            text = prepared.ocr_text if prepared is not None else ""
            if not text and prepared is not None and prepared.blobs:
                text = vision.ocr_images(prepared.blobs)
            self._dispatch.emit(
                lambda: self._start_chat(
                    conversation, system_prompt, user_prompt, [], text, on_reply
                )
            )

        threading.Thread(
            target=work, name="qq-bridge-vision-retry", daemon=True
        ).start()

    def _pick_extras(self, conversation: str, text: str) -> tuple[list[str], list[str]]:
        faces: list[str] = []
        images: list[str] = []
        if self.settings["auto_face"]:
            face_ids = self.settings["face_ids"]
            habit_chance = analysis.habit_face_chance(self.store.habit(conversation))
            chance = max(self.settings["face_probability"], habit_chance * 0.5)
            if face_ids and random.random() < chance:
                faces.append(random.choice(face_ids))
        stickers = self.settings["sticker_paths"]
        if stickers and random.random() < self.settings["sticker_probability"]:
            images.append(random.choice(stickers))
        return faces, images

    # ---------- 收藏表情 ----------

    def custom_face_items(self) -> list[dict[str, str]]:
        return [dict(item) for item in self._custom_faces]

    def refresh_custom_faces_async(
        self, callback: Callable[[list[dict[str, str]]], None] | None = None
    ) -> None:
        client = self.client
        if client is None or self._custom_faces_loading:
            if callback is not None:
                callback(self.custom_face_items())
            return
        self._custom_faces_loading = True

        def work() -> None:
            try:
                faces = _parse_custom_faces(client.fetch_custom_face_detail(50))
            except Exception as exc:
                faces = []
                self._log(f"读取收藏表情失败：{exc}")

            def done() -> None:
                self._custom_faces_loading = False
                self._custom_faces = faces
                if callback is not None:
                    try:
                        callback(self.custom_face_items())
                    except Exception as exc:
                        self._log(f"收藏表情回调出错：{exc}")

            self._dispatch.emit(done)

        threading.Thread(
            target=work, name="qq-bridge-custom-face", daemon=True
        ).start()

    def _pick_custom_face(self) -> str:
        if not self.settings["custom_face_enabled"]:
            return ""
        ids = [item for item in self.settings["custom_face_ids"] if item]
        if not ids:
            ids = [
                str(item.get("emoji_id", ""))
                for item in self._custom_faces
                if item.get("emoji_id")
            ]
        if not ids:
            return ""
        if random.random() >= self.settings["custom_face_probability"]:
            return ""
        return random.choice(ids)

    def _send_custom_face(self, conversation: str, emoji_id: str) -> None:
        client = self.client
        if client is None:
            return
        kind, target = split_conversation(conversation)

        def work() -> None:
            try:
                if kind == "group":
                    client.send_custom_face(emoji_id, group_id=target)
                else:
                    client.send_custom_face(emoji_id, user_id=target)
            except Exception as exc:
                self._log(f"发送收藏表情失败：{exc}")

        threading.Thread(
            target=work, name="qq-bridge-custom-face-send", daemon=True
        ).start()

    def _send_segments(
        self, conversation: str, segments: list[dict[str, Any]], echo: str = ""
    ) -> None:
        client = self.client
        if client is None:
            return

        def work() -> None:
            try:
                client.send(conversation, segments)
            except Exception as exc:
                self._dispatch.emit(
                    lambda error=exc: self._send_failed(conversation, error)
                )
                return
            if echo and self.settings["local_echo"]:
                self._dispatch.emit(lambda: self._say_main(echo[:200], 4000))

        threading.Thread(target=work, name="qq-bridge-send", daemon=True).start()

    def _send_failed(self, conversation: str, exc: Exception) -> None:
        self._log(f"发送到 {conversation} 失败：{exc}")
        self._say_main("QQ 消息没发出去，检查一下 SnowLuma 是否在线。", 5000)

    def _reply_local(self, conversation: str, text: str, max_chars: int = 1500) -> None:
        client = self.client
        if client is None:
            return
        chunks = split_text(text, max_chars)
        if not chunks:
            return

        def work() -> None:
            for chunk in chunks:
                try:
                    client.send(conversation, build_segments(chunk))
                except Exception as exc:
                    self._dispatch.emit(
                        lambda error=exc: self._send_failed(conversation, error)
                    )
                    return

        threading.Thread(target=work, name="qq-bridge-reply", daemon=True).start()

    # ---------- 指令 ----------

    def _handle_command(self, command: Command, message: Any) -> None:
        name = command.name
        conversation = message.conversation
        prefix = self.settings["command_prefix"]

        if name in ("help", "h", "?", "帮助"):
            self._reply_local(conversation, help_text(prefix))
            return
        if name in ("status", "状态"):
            self._reply_local(conversation, self._status_text())
            return
        if name in ("check", "连接"):
            self._reply_local(conversation, "正在检查 SnowLuma 连接…", 200)
            self.check_async(conversation, manual=True)
            return
        if name == "persona":
            self._command_persona(command, conversation)
            return
        if name in ("memory", "记忆"):
            self._command_memory(command, conversation)
            return
        if name in ("slang", "黑话"):
            self._command_slang(command, conversation)
            return
        if name in ("allow", "白名单"):
            self._command_allow(command, message)
            return
        if name == "say":
            self._command_say(command, conversation)
            return
        if name in ("voice", "语音"):
            self._command_voice(command, conversation)
            return
        if name in ("qzone", "空间"):
            self._command_qzone(command, conversation)
            return
        if name in ("compact", "整理"):
            self._compact_notify = conversation
            self._reply_local(conversation, "开始整理记忆和黑话…", 200)
            self._maybe_compact(force=True)
            return
        self._reply_local(conversation, "不认识的指令。\n" + help_text(prefix))

    def _command_persona(self, command: Command, conversation: str) -> None:
        action = (command.args[0].lower() if command.args else "show")
        if action in ("show", "查看", "list"):
            self._reply_local(conversation, "当前人格卡：\n" + self.store.persona(), 2000)
            return
        if action in ("reset", "重置"):
            text = DEFAULT_PERSONA
            try:
                text = self.persona_default.read_text(encoding="utf-8").strip() or text
            except OSError:
                pass
            self.store.set_persona(text)
            self._reply_local(conversation, "人格卡已恢复默认。")
            return
        body = _tail_after_first(command.rest) if action in ("set", "设置") else command.rest.strip()
        if not body:
            self._reply_local(conversation, "用法：/persona set 新的角色设定")
            return
        try:
            self.store.set_persona(body)
        except ValueError as exc:
            self._reply_local(conversation, f"没改成：{exc}")
            return
        self._reply_local(conversation, "人格卡已更新。")

    def _command_memory(self, command: Command, conversation: str) -> None:
        action = (command.args[0].lower() if command.args else "list")
        if action in ("list", "列表"):
            items = self.store.memories(20)
            if not items:
                self._reply_local(conversation, "还没有长期记忆。")
                return
            lines = [f"{index + 1}. {item.get('text', '')}" for index, item in enumerate(items)]
            self._reply_local(conversation, "长期记忆：\n" + "\n".join(lines), 2000)
            return
        if action in ("add", "添加"):
            body = _tail_after_first(command.rest)
            if not body:
                self._reply_local(conversation, "用法：/memory add 要记住的事")
                return
            item = self.store.add_memory(body, importance=60, source="manual")
            self.store.prune_memory(self.settings["memory_max_items"])
            self.store.flush()
            self._reply_local(conversation, "记住了。" if item else "这条内容为空。")
            return
        if action in ("del", "delete", "删除"):
            keyword = _tail_after_first(command.rest)
            if not keyword:
                self._reply_local(conversation, "用法：/memory del 关键词")
                return
            removed = self.store.remove_memory(keyword)
            self.store.flush()
            self._reply_local(conversation, f"删除了 {removed} 条记忆。")
            return
        self._reply_local(conversation, "用法：/memory list | add 内容 | del 关键词")

    def _command_slang(self, command: Command, conversation: str) -> None:
        action = (command.args[0].lower() if command.args else "list")
        if action in ("list", "列表"):
            terms = self.store.slang_terms()
            if not terms:
                self._reply_local(conversation, "黑话库还是空的。")
                return
            ordered = sorted(
                terms.values(),
                key=lambda item: int(item.get("count", 0)),
                reverse=True,
            )
            lines = [
                f"{item.get('term', '')}：{item.get('meaning', '')}"
                for item in ordered[:25]
                if isinstance(item, dict)
            ]
            self._reply_local(conversation, "黑话库：\n" + "\n".join(lines), 2000)
            return
        if action in ("add", "添加"):
            body = _tail_after_first(command.rest)
            if not body:
                self._reply_local(conversation, "用法：/slang add 词=含义，或 /slang add 词 含义")
                return
            if "=" in body:
                term, meaning = body.split("=", 1)
            elif "：" in body:
                term, meaning = body.split("：", 1)
            else:
                parts = body.split(None, 1)
                term = parts[0]
                meaning = parts[1] if len(parts) > 1 else ""
            entry = self.store.add_slang(term, meaning, source="manual")
            self.store.prune_slang(self.settings["slang_max_terms"])
            self.store.flush()
            self._reply_local(
                conversation, "收进黑话库了。" if entry else "词或含义为空，没收录。"
            )
            return
        if action in ("del", "delete", "删除"):
            term = _tail_after_first(command.rest)
            if not term:
                self._reply_local(conversation, "用法：/slang del 词")
                return
            removed = self.store.remove_slang(term)
            self.store.flush()
            self._reply_local(conversation, "删掉了。" if removed else "黑话库里没有这个词。")
            return
        self._reply_local(conversation, "用法：/slang list | add 词=含义 | del 词")

    def _command_allow(self, command: Command, message: Any) -> None:
        conversation = message.conversation
        action = (command.args[0].lower() if command.args else "list")
        if action in ("list", "列表"):
            private = self.settings["allow_private"]
            groups = self.settings["allow_groups"]
            notes = self.settings["allow_notes"]

            def tagged(kind: str, values: list[str]) -> str:
                items = []
                for value in values:
                    note = notes.get(f"{kind}:{value}")
                    items.append(f"{value}（{note}）" if note else value)
                return "、".join(items)

            text = "白名单："
            text += "\n好友：" + (tagged("private", private) if private else "（空）")
            text += "\n群：" + (tagged("group", groups) if groups else "（空）")
            if self.owner:
                text += f"\n主人：{self.owner}"
            else:
                text += "\n主人：未设置（在插件设置里填 owner_qq）"
            self._reply_local(conversation, text)
            return
        if action not in ("add", "添加", "del", "delete", "删除"):
            self._reply_local(
                conversation,
                "用法：/allow list | add 123456 [备注] | add group:123456 [备注] | del 123456",
            )
            return
        target_text = _tail_after_first(command.rest)
        note = ""
        if not target_text:
            kind, target = split_conversation(conversation)
        else:
            parts = target_text.split(None, 1)
            note = parts[1].strip() if len(parts) > 1 else ""
            kind, target = _parse_allow_target(parts[0])
            if kind == "invalid":
                self._reply_local(conversation, "格式不对：用 123456 或 group:123456。")
                return
        if not target:
            self._reply_local(conversation, "缺少 QQ 号或群号。")
            return
        adding = action in ("add", "添加")
        result = self._allow_target(
            kind, target, add=adding, note=note if adding else None
        )
        self._reply_local(conversation, result)

    def _command_say(self, command: Command, conversation: str) -> None:
        parts = command.rest.split(None, 1)
        if len(parts) < 2:
            self._reply_local(conversation, "用法：/say group:123456 内容")
            return
        target_text, body = parts[0], parts[1].strip()
        kind, target = _parse_allow_target(target_text)
        if kind == "invalid" or not target:
            self._reply_local(conversation, "目标格式不对：用 group:123456 或 private:123456。")
            return
        target_conversation = f"{kind}:{target}"
        if not self.is_allowed(target_conversation):
            self._reply_local(conversation, "这个对象不在白名单里，先 /allow add 再加。")
            return
        cleaned, faces = parse_face_tags(body)
        segments = build_segments(cleaned, faces)
        if not segments:
            self._reply_local(conversation, "内容为空。")
            return
        self._send_segments(target_conversation, segments, cleaned)
        self._reply_local(conversation, f"已发送到 {target_conversation}。")

    def _command_voice(self, command: Command, conversation: str) -> None:
        target_conversation = conversation
        body = command.rest.strip()
        if command.args:
            first = command.args[0]
            if ":" in first or first.isdigit():
                kind, target = _parse_allow_target(first)
                if kind == "invalid" or not target:
                    self._reply_local(
                        conversation, "目标格式不对：用 group:123456 或 private:123456。"
                    )
                    return
                target_conversation = f"{kind}:{target}"
                parts = command.rest.split(None, 1)
                body = parts[1].strip() if len(parts) > 1 else ""
        if not body:
            self._reply_local(
                conversation,
                "用法：/voice 要读的文字，或 /voice group:123456 要读的文字",
            )
            return

        def done(ok: bool, message: str) -> None:
            self._reply_local(conversation, message)

        self.send_tts(target_conversation, body, done)

    def _command_qzone(self, command: Command, conversation: str) -> None:
        action = (command.args[0].lower() if command.args else "")
        if action in ("on", "off", "开", "关"):
            enabled = action in ("on", "开")
            self.settings["qzone_enabled"] = enabled
            try:
                self.api.save_settings({"qzone_enabled": enabled})
            except Exception as exc:
                self._log(f"保存 QQ 空间开关失败：{exc}")
            self._reply_local(conversation, "QQ 空间发送已开启。" if enabled else "QQ 空间发送已关闭。")
            return
        if action in ("list", "列表"):
            self._qzone_worker(conversation, "list")
            return
        if action in ("del", "delete", "删除"):
            tid = _tail_after_first(command.rest)
            if not tid:
                self._reply_local(conversation, "用法：/qzone del 说说id")
                return
            self._qzone_worker(conversation, "delete", tid)
            return
        if not self.settings["qzone_enabled"]:
            self._reply_local(
                conversation,
                "QQ 空间发送还没开启。先在插件设置里打开 qzone_enabled，或发 /qzone on。",
            )
            return
        body = command.rest.strip()
        if not body:
            self._reply_local(conversation, "用法：/qzone 要发的内容")
            return
        self._qzone_worker(conversation, "publish", body)

    def _qzone_worker(self, conversation: str, action: str, value: str = "") -> None:
        client = self.client
        if client is None:
            self._reply_local(conversation, "OneBot 客户端还没准备好。")
            return

        def work() -> None:
            try:
                if action == "publish":
                    client.publish_qzone(
                        value,
                        QZONE_RIGHTS[self.settings["qzone_permission"]],
                        target_uins=self.settings["qzone_target_uins"],
                    )
                    result = "已发到 QQ 空间。"
                elif action == "delete":
                    client.delete_qzone(value)
                    result = "那条说说已删除。"
                else:
                    data = client.list_qzone(10)
                    result = _format_qzone(data)
            except Exception as exc:
                result = f"QQ 空间操作失败：{exc}"
            self._dispatch.emit(lambda: self._reply_local(conversation, result, 2000))

        threading.Thread(target=work, name="qq-bridge-qzone", daemon=True).start()

    # ---------- QQ 空间自动回复 ----------

    def _maybe_qzone_auto_reply(self) -> None:
        if not self.settings["qzone_auto_reply"] or not self.started:
            return
        client = self.client
        if client is None or self._qzone_checking:
            return
        now = time.time()
        interval = self.settings["qzone_reply_interval"] * 60
        last = float(self.store.runtime.get("qzone_last_check", 0) or 0)
        if last and now - last < interval:
            return
        self._qzone_checking = True
        self.store.mark_qzone_check(now)
        self.store.flush()

        def work() -> None:
            try:
                all_ids, candidates = self._collect_qzone_candidates(client)
                error = ""
            except Exception as exc:
                all_ids, candidates, error = [], [], str(exc)
            self._dispatch.emit(
                lambda: self._finish_qzone_scan(all_ids, candidates, error)
            )

        threading.Thread(
            target=work, name="qq-bridge-qzone-auto", daemon=True
        ).start()

    def _collect_qzone_candidates(
        self, client: OneBotClient
    ) -> tuple[list[str], list[dict[str, Any]]]:
        login = client.get_login_info() or {}
        self_id = str(login.get("user_id", "") or "").strip()
        items = _qzone_items(client.list_qzone(5))[:5]
        all_ids: list[str] = []
        candidates: list[dict[str, Any]] = []
        for post in items:
            tid = _first_text(post, ("tid", "id"), 80)
            if not tid:
                continue
            try:
                count = int(post.get("comment_num", 0) or 0)
            except (TypeError, ValueError):
                count = 0
            if count <= 0:
                continue
            try:
                detail = client.qzone_detail(tid, num=50)
            except Exception as exc:
                self._log(f"读取空间评论失败（{tid}）：{exc}")
                continue
            post_text = _first_text(post, ("content", "text", "summary"), 300)
            for comment in _qzone_comments(detail):
                cid = _comment_id(comment)
                if cid:
                    all_ids.append(cid)
                uin = _comment_uin(comment)
                content = _comment_text(comment)
                if not cid or not content:
                    continue
                if self_id and uin == self_id:
                    continue
                if self.store.qzone_replied(cid):
                    continue
                candidates.append(
                    {
                        "tid": tid,
                        "target_uin": self_id,
                        "comment_id": cid,
                        "uin": uin,
                        "name": _comment_name(comment) or uin or "有人",
                        "content": content,
                        "post": post_text,
                    }
                )
        return all_ids, candidates

    def _finish_qzone_scan(
        self,
        all_ids: list[str],
        candidates: list[dict[str, Any]],
        error: str,
    ) -> None:
        self._qzone_checking = False
        if not self.started:
            return
        if error:
            self._log(f"QQ 空间自动回复检查失败：{error}")
            return
        if not self.store.qzone_baseline_done():
            for cid in all_ids:
                self.store.mark_qzone_replied(cid)
            self.store.mark_qzone_baseline(True)
            self.store.flush()
            if candidates:
                self._log(
                    f"QQ 空间自动回复已就绪，跳过 {len(candidates)} 条旧评论。"
                )
            return
        pending = [
            item
            for item in candidates
            if not self.store.qzone_replied(item.get("comment_id", ""))
        ]
        if not pending:
            return
        self._qzone_queue = pending
        self._qzone_left = max(1, int(self.settings["qzone_reply_max"]))
        self._next_qzone_reply()

    def _next_qzone_reply(self) -> None:
        if not self.started or self._qzone_left <= 0 or not self._qzone_queue:
            self._qzone_queue.clear()
            return
        item = self._qzone_queue.pop(0)
        if self.store.qzone_replied(item.get("comment_id", "")):
            self._next_qzone_reply()
            return
        memory = (
            analysis.memory_prompt(self.store.memories(6))
            if self.settings["memory_enabled"]
            else ""
        )
        slang = (
            analysis.slang_prompt(self.store.slang_terms(), item["content"])
            if self.settings["slang_enabled"]
            else ""
        )
        system_prompt = analysis.build_system_prompt(
            self.store.persona(), memory, slang, ""
        )
        system_prompt += (
            "\n\n现在你在 QQ 空间回复别人的评论。只写一句自然的中文回复，"
            "不要 Markdown、不要解释、不要输出 [silent]，不超过 60 个字。"
        )
        user_prompt = (
            f"我说说：{item.get('post') or '（无正文）'}\n"
            f"{item.get('name') or item.get('uin') or '有人'} 评论：{item.get('content')}\n"
            "请回复这条评论。"
        )

        def on_reply(reply: str | None, error: str | None) -> None:
            if error:
                self._log(f"QQ 空间自动回复生成失败：{error}")
                self._qzone_left -= 1
                self._next_qzone_reply()
                return
            cleaned = sanitize_reply(reply or "", 120)
            if not cleaned:
                self._qzone_left -= 1
                self._next_qzone_reply()
                return

            def post() -> None:
                client = self.client
                if client is None:
                    self._dispatch.emit(
                        lambda: self._after_qzone_post(
                            item, False, "OneBot 客户端还没准备好。"
                        )
                    )
                    return
                try:
                    client.comment_qzone(
                        item["tid"], cleaned, target_uin=item.get("target_uin", "")
                    )
                    ok, message = True, ""
                except Exception as exc:
                    ok, message = False, str(exc)
                self._dispatch.emit(
                    lambda: self._after_qzone_post(item, ok, message)
                )

            threading.Thread(
                target=post, name="qq-bridge-qzone-comment", daemon=True
            ).start()

        try:
            self.api.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                on_reply,
            )
        except Exception as exc:
            self._log(f"发起空间自动回复失败：{exc}")
            self._qzone_left -= 1
            self._next_qzone_reply()

    def _after_qzone_post(
        self, item: dict[str, Any], ok: bool, message: str
    ) -> None:
        self._qzone_left -= 1
        if ok:
            self.store.mark_qzone_replied(str(item.get("comment_id", "")))
            self.store.flush()
            self._log(
                f"已自动回复 {item.get('name') or item.get('uin') or '有人'} 的空间评论。"
            )
        else:
            self._log(f"QQ 空间评论发送失败：{message}")
        self._next_qzone_reply()

    def _status_text(self) -> str:
        connection = self.store.runtime.get("connection_ok")
        if connection is True:
            state = "已连接"
        elif connection is False:
            state = "连接失败"
        else:
            state = "还没检查"
        port = self.server.bound_port if self.server is not None else 0
        lines = [
            f"SnowLuma：{state}（{self.settings['snowluma_http']}）",
            f"事件端口：{port or '未启动'}",
            f"白名单：好友 {len(self.settings['allow_private'])} 个，群 {len(self.settings['allow_groups'])} 个；"
            f"盯人 {len(self.settings['watch_users'])} 个",
            f"记忆：{len(self.store.memories(500))} 条；黑话：{len(self.store.slang_terms())} 条",
            f"QQ 空间：{'开启' if self.settings['qzone_enabled'] else '关闭'}；"
            f"自动回复{'开启' if self.settings['qzone_auto_reply'] else '关闭'}",
        ]
        return "\n".join(lines)

    # ---------- 连接检查 ----------

    def check_async(self, conversation: str = "", manual: bool = False) -> None:
        client = self.client
        if client is None or self._checking:
            return
        self._checking = True

        def work() -> None:
            try:
                info = client.get_login_info()
                user_id = str(info.get("user_id", "") or "")
                nickname = str(info.get("nickname", "") or "")
                text = f"SnowLuma 已连接：{nickname or 'QQ'}（{user_id}）"
                ok = True
            except Exception as exc:
                text = f"SnowLuma 连接失败：{exc}"
                ok = False
            self._dispatch.emit(
                lambda: self._after_check(ok, text, conversation, manual)
            )

        threading.Thread(target=work, name="qq-bridge-check", daemon=True).start()

    def _after_check(
        self, ok: bool, text: str, conversation: str, manual: bool = False
    ) -> None:
        self._checking = False
        self._last_check = time.time()
        if not self.started:
            return
        self.store.set_connection(ok)
        self.store.flush()
        self._log(text)
        if conversation and ok:
            self._reply_local(conversation, text, 600)
        elif not ok and (manual or self.settings["announce"]):
            self._say_main(text[:200], 6000)

    # ---------- 记忆整理 ----------

    def _maybe_compact(self, force: bool = False, callback: Callable[[str], None] | None = None) -> None:
        def report(text: str) -> None:
            if callback is None:
                return
            try:
                callback(text)
            except Exception as exc:
                self._log(f"整理回调出错：{exc}")

        if self._compacting:
            report("已经在整理中了，稍等。")
            return
        if not (self.settings["memory_enabled"] or self.settings["slang_enabled"]):
            report("长期记忆和黑话库都关着，先在“高级”页打开。")
            return
        now = time.time()
        if not force and now < self._compact_cooldown_until:
            return
        inbox = self.store.inbox()
        minimum = 3 if force else self.settings["memory_min_batch"]
        if len(inbox) < minimum:
            if force and self._compact_notify:
                self._reply_local(self._compact_notify, "聊天记录太少，先不用整理。")
                self._compact_notify = ""
            report("聊天记录太少，先不用整理。")
            return
        last = float(self.store.runtime.get("last_compact_at", 0) or 0)
        interval = self.settings["memory_interval_minutes"] * 60
        if not force and now - last < interval:
            return

        # 单次整理的记录不宜太多：模型回复上限有限，输入太长也更容易超时。
        batch_size = max(3, min(int(self.settings["memory_batch_size"]), 30))
        batch = inbox[-batch_size:]
        prompt = analysis.compaction_prompt(batch, self.store.slang_terms().keys())
        self._compacting = True
        self._compact_started_at = now
        attempts = 0

        def fail(reason: str) -> None:
            self._compacting = False
            self._compact_cooldown_until = time.time() + 300
            self._log(f"记忆整理失败：{reason}")
            notify = self._compact_notify
            self._compact_notify = ""
            if notify:
                self._reply_local(notify, f"整理失败：{reason}")
            report(f"整理失败：{reason}")

        def request(short: bool = False) -> None:
            nonlocal attempts
            attempts += 1
            # 第二次尝试缩短输入和条数，降低思考预算和输出长度的压力。
            request_prompt = prompt
            if short:
                request_prompt = analysis.compaction_prompt(
                    batch[-12:], self.store.slang_terms().keys(), short=True
                )

            def on_done(reply: str | None, error: str | None) -> None:
                if error:
                    # 网络抖动、限流、5xx 重试一次；输入太长导致的超时也能借此恢复。
                    if attempts < 2 and analysis.transient_ai_error(error):
                        self._log(f"记忆整理失败，换短一点的输入重试一次：{error}")
                        request(short=True)
                        return
                    fail(error)
                    return

                content = reply or ""
                memories, slang = analysis.parse_compaction(content)
                if not memories and not slang and not analysis.looks_like_compaction(content):
                    # 模型没按 JSON 返回时不能清空聊天记录，否则这批内容就永久丢了。
                    if attempts < 2:
                        self._log("模型没有返回可用的 JSON，换短一点的输入重试一次。")
                        request(short=True)
                        return
                    fail("模型没有返回可用的 JSON，聊天记录已保留，稍后再试")
                    return

                try:
                    for text in memories:
                        self.store.add_memory(text, importance=45, source="auto")
                    for item in slang:
                        self.store.add_slang(
                            item.get("term", ""),
                            item.get("meaning", ""),
                            item.get("example", ""),
                            source="auto",
                        )
                    self.store.prune_memory(self.settings["memory_max_items"])
                    self.store.prune_slang(self.settings["slang_max_terms"])
                    self.store.clear_inbox(keep=3)
                    self.store.mark_compacted()
                    self.store.flush()
                except Exception as exc:
                    fail(f"保存整理结果出错：{exc}")
                    return

                self._compacting = False
                if memories or slang:
                    self._log(
                        f"记忆整理完成：新增 {len(memories)} 条记忆、{len(slang)} 条黑话。"
                    )
                    summary = f"整理完成：新增 {len(memories)} 条记忆、{len(slang)} 条黑话。"
                else:
                    self._log("记忆整理完成：没有新的可记内容。")
                    summary = "整理完成：没有新的可记内容。"
                notify = self._compact_notify
                self._compact_notify = ""
                if notify:
                    self._reply_local(notify, summary)
                report(summary)

            try:
                self.api.chat(
                    [
                        {
                            "role": "system",
                            "content": "你是聊天记录整理助手，只输出 JSON，不要输出其他内容。",
                        },
                        {"role": "user", "content": request_prompt},
                    ],
                    on_done,
                )
            except Exception as exc:
                fail(f"发起请求失败：{exc}")

        request()


def _tail_after_first(rest: str) -> str:
    parts = str(rest or "").strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _parse_allow_target(value: str) -> tuple[str, str]:
    text = str(value or "").strip()
    lowered = text.lower()
    for prefix in ("group:", "群:", "群聊:", "g:"):
        if lowered.startswith(prefix):
            return "group", "".join(ch for ch in text[len(prefix) :] if ch.isdigit())
    for prefix in ("private:", "好友:", "私聊:", "p:"):
        if lowered.startswith(prefix):
            return "private", "".join(ch for ch in text[len(prefix) :] if ch.isdigit())
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return "private", digits
    return "invalid", ""


def _format_qzone(data: Any) -> str:
    items: Any = data
    if isinstance(data, dict):
        for key in ("list", "feeds", "messages", "data", "msglist"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
    if not isinstance(items, list) or not items:
        return "最近没有读到说说。"
    lines = ["最近的说说："]
    for item in items[:5]:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("text") or item.get("summary") or ""
        tid = item.get("tid") or item.get("id") or ""
        content = " ".join(str(content).split())[:60]
        lines.append(f"- {content or '（无正文）'}" + (f"  [{tid}]" if tid else ""))
    return "\n".join(lines) if len(lines) > 1 else "最近没有读到说说。"


def _qzone_items(data: Any) -> list[dict[str, Any]]:
    items: Any = data
    if isinstance(data, dict):
        for key in ("list", "feeds", "messages", "data", "msglist"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _qzone_comments(data: Any) -> list[dict[str, Any]]:
    comments: Any = data
    if isinstance(data, dict):
        comments = data.get("comments")
    if not isinstance(comments, list):
        return []
    return [item for item in comments if isinstance(item, dict)]


def _first_text(item: dict[str, Any], keys: tuple[str, ...], limit: int = 400) -> str:
    for key in keys:
        value = item.get(key)
        if value is None:
            continue
        text = " ".join(str(value).split()).strip()
        if text:
            return text[:limit]
    return ""


def _comment_id(item: dict[str, Any]) -> str:
    return _first_text(item, ("comment_id", "id", "cid", "tid"), 80)


def _comment_uin(item: dict[str, Any]) -> str:
    return _first_text(item, ("uin", "user_id", "qq"), 40)


def _comment_name(item: dict[str, Any]) -> str:
    return _first_text(item, ("name", "nickname", "nick", "card"), 40)


def _comment_text(item: dict[str, Any]) -> str:
    return _first_text(item, ("content", "text", "message"), 400)


def _parse_custom_faces(data: Any) -> list[dict[str, str]]:
    items: Any = data
    if isinstance(data, dict):
        for key in ("list", "data", "faces", "items"):
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, dict):
            emoji_id = str(
                item.get("emoji_id") or item.get("emojiId") or item.get("id") or ""
            ).strip()
            if not emoji_id or emoji_id in seen:
                continue
            seen.add(emoji_id)
            result.append(
                {
                    "emoji_id": emoji_id,
                    "desc": str(item.get("desc") or "").strip()[:60],
                    "url": str(item.get("url") or "").strip(),
                }
            )
        elif isinstance(item, str) and item.strip() and item.strip() not in seen:
            seen.add(item.strip())
            result.append({"emoji_id": item.strip(), "desc": "", "url": ""})
        if len(result) >= 200:
            break
    return result
