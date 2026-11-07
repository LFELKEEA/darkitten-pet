"""桌宠的深度求索娘服务：模型、动作、反应和台词。"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from pathlib import Path

from .clips import HOLD_SUFFIX, action_for_host, build_clips, build_hold_clips
from .lines import PERSONA_PROMPT, greeting_for_hour, pick, reaction_line
from .menu import build_menu
from .model_source import (
    discover_models,
    extract_archive,
    find_model_file,
    is_archive,
    locate,
    watermark_switch,
)

IDLE_GESTURES = [
    "tilt",
    "glance",
    "sigh",
    "listen",
    "nod",
    "shake",
    "proud",
    "wave",
    "stretch",
]
CUTE_GESTURES = ["shy", "love", "squint", "pout", "dance", "sing"]
# 只有这几个手型/小道具动作参与待机自动播放：播一遍就退回默认，
# 不会把姿势留在身上。换装、变小、藏/露尾巴和特殊动作会改变外形，
# 自动播放会让人莫名其妙地一直变样，所以只允许主动选择。
POSE_GESTURES = ["hand_up", "hand_down", "foxtail_out", "foxtail_in", "point_out", "point_in"]

# 模型参数只能让头、手、身子变形，做不出「整个人离开地面」或「整个人转一圈」。
# 这两个动作额外借用宿主已有的整图动画，动作名才和实际表现对得上。
HOST_ACTION_FOR_CLIP = {
    "hop": "hop",
    "dance": "spin",
}

PRAISE_WORDS = ("喜欢", "可爱", "厉害", "好看", "棒", "谢谢", "爱你", "聪明", "牛")
SCOLD_WORDS = ("笨", "慢", "没用", "垃圾", "蠢", "人工智障", "错了", "不对")
QUESTION_WORDS = ("怎么", "为什么", "如何", "?", "？", "报错", "求助")
GREETING_WORDS = ("你好", "在吗", "早上好", "晚上好", "早安", "晚安", "嗨", "hello")
LAUGH_WORDS = ("哈哈", "笑死", "233", "hhh")


class DeepSeekChanService:
    def __init__(self, api) -> None:
        self.api = api
        self.pet = api.manager.pet
        self.data_dir = Path(getattr(api.manager, "data_dir", Path(".")))
        self.settings = dict(api.settings)
        self.state = api.storage
        self.model_path: Path | None = None
        self.clips: dict = {}
        self.hold_clips: dict = {}
        self.expressions: dict = {}
        self.ready = False
        self._started = False
        self._thread: threading.Thread | None = None
        self._thread_result: Path | None = None
        self._thread_error = ""
        self._load_started_at = 0.0
        self._load_announced = False
        self._next_idle = 0.0
        self._next_chatter = 0.0
        self._last_action = ""
        self._played_clips: set[str] = set()
        self._pat_count = int(self.state.get("pat_count") or 0)
        self._poke_count = int(self.state.get("poke_count") or 0)
        self._stop_event = threading.Event()
        self._prompt_handle = None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._migrate_settings()

        try:
            self.api.set_lines(self._line_for)
        except Exception as exc:
            self.api.log(f"替换台词失败：{exc}")

        try:
            self._prompt_handle = self.api.register_prompt_provider(self._prompt)
        except Exception as exc:
            self.api.log(f"注册人格失败：{exc}")

        self.api.on("pet.pat", self._on_pat)
        self.api.on("pet.poke", self._on_poke)
        self.api.on("chat.user", self._on_chat_user)
        self.api.on("chat.reply", self._on_chat_reply)

        self._next_idle = time.monotonic() + self._idle_gap()
        self._next_chatter = time.monotonic() + self._chatter_gap()
        self.api.every(1000, self._tick)

        self._begin_locate()

    def stop(self) -> None:
        self._started = False
        self._stop_event.set()
        try:
            if self._prompt_handle is not None:
                self.api.unregister_prompt_provider(self._prompt_handle)
        except Exception:
            pass
        self._prompt_handle = None
        try:
            self.api.clear_lines()
        except Exception:
            pass
        try:
            self.api.model.clear_action_menu()
        except Exception:
            pass
        try:
            self.api.model.unload()
        except Exception:
            pass
        try:
            self.api.save_storage()
        except Exception:
            pass

    # ---------- 找模型 ----------

    def _migrate_settings(self) -> None:
        """1.0.2 及以前默认 30 帧；升级后一次性提到 60 帧，动作更顺。"""
        try:
            version = int(self.settings.get("settings_version") or 1)
        except (TypeError, ValueError):
            version = 1
        if version >= 2:
            return
        changes: dict = {"settings_version": 2}
        try:
            fps = int(self.settings.get("fps") or 30)
        except (TypeError, ValueError):
            fps = 30
        if fps <= 30:
            changes["fps"] = 60
        self.settings.update(changes)
        try:
            self.api.save_settings(changes)
        except Exception:
            pass

    def _begin_locate(self) -> None:
        location = locate(self.settings)
        if location is None:
            self.api.log("没有找到 Live2D 模型")
            self.api.after(2600, lambda: self.api.say(pick("no_model"), 8000))
            return
        if location.ready:
            self._load(location.model)
            return
        if location.archive is not None:
            self._extract_async(location.archive)
            return
        if location.directory is not None:
            model = find_model_file(location.directory)
            if model is not None:
                self._load(model)
                return
        self.api.after(2600, lambda: self.api.say(pick("no_model"), 8000))

    def _extract_async(self, archive: Path) -> None:
        self.api.log(f"从压缩包准备模型：{archive}")
        self.api.after(
            600,
            lambda: self.api.say("第一次用这个模型，我先从压缩包里搬出来……", 5200),
        )
        self._thread_error = ""
        self._thread_result = None

        def worker() -> None:
            try:
                result = extract_archive(archive, self.data_dir)
            except Exception as exc:  # 解包失败交给主线程提示
                self._thread_error = f"{type(exc).__name__}: {exc}"
                return
            self._thread_result = result

        self._thread = threading.Thread(target=worker, name="deepseek-chan-extract", daemon=True)
        self._thread.start()

    def _check_extraction(self) -> None:
        thread = self._thread
        if thread is None or thread.is_alive():
            return
        self._thread = None
        if self._thread_result is not None:
            self._load(self._thread_result)
            return
        self.api.log(f"解包失败：{self._thread_error}")
        self.api.say(
            "模型解包失败了，可能是压缩包不完整，或者本机没有解压 RAR/7Z 的工具。"
            "可以先用 7-Zip / WinRAR 解压成文件夹，再到设置里选择模型文件夹。",
            9000,
        )

    def _load(self, model_path: Path | None) -> None:
        if model_path is None:
            return
        model_path = Path(model_path)
        if model_path.is_dir():
            model_path = find_model_file(model_path)
            if model_path is None:
                self.api.say(pick("no_model"), 8000)
                return
        if is_archive(model_path):
            self._extract_async(model_path)
            return
        if not model_path.is_file():
            self.api.say(pick("no_model"), 8000)
            return
        model_api = getattr(self.api, "model", None)
        if model_api is None:
            self.api.say(
                "这个皮肤要桌宠 1.4.0 以上才能换装。先把 LFELKEEA 更新一下，再来找我。",
                8000,
            )
            self.api.log("宿主没有 pet.model 接口，需要 LFELKEEA 1.4.0 或更新版本")
            return
        available = False
        try:
            available = bool(model_api.available)
        except Exception as exc:
            available = False
            self.api.log(f"检查 Live2D 运行库失败：{exc}")
        if not available:
            self.api.say(
                "Live2D 运行库没准备好。如果是旧版桌宠，先升级到 1.4.0；新版还这样就把 vendor 目录一起解压。",
                9000,
            )
            self.api.log("Live2D runtime unavailable")
            return

        self.model_path = model_path
        self.ready = False
        self._load_started_at = time.monotonic()
        self._load_announced = False
        try:
            fps = int(self.settings.get("fps", 60) or 60)
            self.api.model.load(
                str(model_path),
                height=int(self.settings.get("height", 760) or 760),
                fps=max(30, min(60, fps)),
                scale=float(self.settings.get("scale", 1.0) or 1.0),
                follow_cursor=bool(self.settings.get("gaze_follow", True)),
            )
            self.api.log(f"开始加载模型：{model_path}")
        except Exception as exc:
            self.api.log(f"加载模型失败：{exc}")
            self.api.say(pick("error"), 6000)

    # ---------- 模型就绪 ----------

    def _on_model_ready(self) -> None:
        self.ready = True
        self._load_announced = True
        self._played_clips.clear()
        model_dir = self.model_path.parent if self.model_path is not None else None
        try:
            self.clips, self.expressions = build_clips(model_dir) if model_dir else ({}, {})
            self.hold_clips = build_hold_clips(self.clips)
            self.api.model.define_clips({**self.clips, **self.hold_clips})
            if self.expressions:
                self.api.model.define_expressions(self.expressions)
            self.api.model.follow_cursor(bool(self.settings.get("gaze_follow", True)))
            self.api.log(
                f"模型就绪：{len(self.clips)} 个动作，{len(self.expressions)} 个表情，"
                f"{len(self.hold_clips)} 个保持姿势"
            )
        except Exception as exc:
            self.api.log(f"定义动作失败：{exc}")

        self.apply_watermark()
        self._install_menu()

        if self.settings.get("persona", True):
            try:
                self.api.say(greeting_for_hour(datetime.now().hour), 4600)
            except Exception:
                pass
        self._play_first(["greet", "wave_motion", "wave", "nod", "tilt"], fade=0.2, weight=0.9)
        if self._should_show_credit() and self.is_deepseek_model():
            self.api.after(5200, lambda: self.api.say(pick("credit"), 9000))
            self.settings["show_credit_on_first_run"] = False
            try:
                self.api.save_settings({"show_credit_on_first_run": False})
            except Exception:
                pass

    def _should_show_credit(self) -> bool:
        return bool(self.settings.get("show_credit_on_first_run", True))

    def _install_menu(self) -> None:
        setter = getattr(self.api.model, "set_action_menu", None)
        if setter is None:
            self.api.log("宿主还不支持替换动作菜单，需要 LFELKEEA 1.4.1 或更新版本")
            return
        if not self.clips and not self.expressions:
            return
        groups = build_menu(
            self.clips,
            self.expressions,
            self._play_menu,
            self._expression,
            self._clear_expression,
            self._reset_action,
        )
        try:
            setter(groups)
        except Exception as exc:
            self.api.log(f"注册动作菜单失败：{exc}")

    def apply_watermark(self) -> None:
        """按模型作者提供的水印开关，隐藏或恢复模型上的水印文字。"""
        model_dir = self.model_path.parent if self.model_path is not None else None
        params = watermark_switch(model_dir)
        if not params:
            return
        hide = bool(self.settings.get("hide_watermark", True))
        values = {param_id: (value if hide else 0.0) for param_id, value in params.items()}
        try:
            self.api.model.params(values)
        except Exception as exc:
            self.api.log(f"设置水印开关失败：{exc}")

    def _clear_expression(self) -> None:
        if not self.ready:
            return
        try:
            self.api.model.clear_expression()
        except Exception as exc:
            self.api.log(f"恢复默认表情失败：{exc}")

    def _reset_action(self) -> None:
        """清掉所有正在播放/停住的姿势，回到模型默认动作。

        这里不用宿主的 stop_all()：它会把表情一起清掉。逐个 stop 已经播过
        的片段（包括 @hold 变体）即可，表情保持不变。
        """
        if not self.ready:
            return
        targets = set(self._played_clips)
        # 兜底：即使某个保持姿势没被记上，也把现存的 hold 变体一起收掉。
        targets.update(self.hold_clips)
        failed = 0
        for name in targets:
            try:
                self.api.model.stop(name)
            except Exception:
                failed += 1
        self._played_clips.clear()
        self.api.log(f"已还原默认动作（清理 {len(targets)} 个片段，失败 {failed} 个）")

    # ---------- 每秒心跳 ----------

    def _tick(self) -> None:
        if self._stop_event.is_set():
            return
        self._check_extraction()

        if not self.ready:
            if self._load_started_at <= 0:
                return
            info = {}
            try:
                info = self.api.model.info
            except Exception:
                info = {}
            if info.get("active"):
                self._on_model_ready()
                return
            error = str(info.get("error") or "")
            if error and not self._load_announced:
                self._load_announced = True
                self.api.log(f"模型加载报错：{error}")
                self.api.say(pick("error"), 6000)
            return

        pet = self.pet
        action = str(getattr(pet, "current_action", "") or "")
        if action != self._last_action:
            self._last_action = action
            clip = action_for_host(action)
            if clip:
                # 宿主自己已经播了整图动画，这里只补模型参数，别再把动画重启一遍。
                self._play(clip, fade=0.14, with_host=False)

        now = time.monotonic()
        if self.settings.get("idle_actions", True) and now >= self._next_idle:
            if not bool(getattr(pet, "sleeping", False)):
                self._idle_gesture()
            self._next_idle = now + self._idle_gap()

        if self.settings.get("chatter", True) and now >= self._next_chatter:
            if not bool(getattr(pet, "sleeping", False)):
                self.api.say(pick("chatter"), 5200)
                self._play(random.choice(["tilt", "glance", "listen"]), fade=0.2, weight=0.8)
            self._next_chatter = now + self._chatter_gap()

    def _idle_gesture(self) -> None:
        roll = random.random()
        if self.settings.get("rich_actions", True) and roll < 0.08:
            pool = self._available(POSE_GESTURES)
        elif roll < 0.28:
            pool = self._available(CUTE_GESTURES)
        else:
            pool = self._available(IDLE_GESTURES)
        if not pool:
            pool = list(self.clips)
        if not pool:
            return
        self._play(random.choice(pool), fade=0.22, weight=0.85)

    def _available(self, names) -> list[str]:
        return [name for name in names if name in self.clips]

    # ---------- 事件 ----------

    def _on_pat(self) -> None:
        self._pat_count += 1
        self.state["pat_count"] = self._pat_count
        try:
            self.api.save_storage()
        except Exception:
            pass
        if not self.settings.get("reactions", True):
            return
        if self._pat_count % 10 == 0:
            self._play_first(["love", "shy", "squint", "nod"], fade=0.25)
            self._expression_first(["heart", "blush"], hold=4.0)
        elif random.random() < 0.3:
            self._play_first(["squint", "shy", "tilt"], fade=0.18)
        else:
            self._play_first(["shy", "tilt", "nod"], fade=0.18)

    def _on_poke(self) -> None:
        self._poke_count += 1
        self.state["poke_count"] = self._poke_count
        try:
            self.api.save_storage()
        except Exception:
            pass
        if not self.settings.get("reactions", True):
            return
        pool = self._available(["surprise", "pout", "squint", "deadpan", "dizzy", "shake", "tilt"])
        if pool:
            self._play(random.choice(pool), fade=0.12)

    def _on_chat_user(self, text: str = "", **_kwargs) -> None:
        if not self.settings.get("reactions", True):
            return
        body = str(text or "")
        compact = body.lower()
        if any(word in compact for word in PRAISE_WORDS):
            self._play_first(["shy", "tilt", "nod"], fade=0.2)
            self._expression_first(["blush"], hold=5.0)
        elif any(word in compact for word in SCOLD_WORDS):
            pool = self._available(["pout", "deadpan", "shake", "sigh"])
            if pool:
                self._play(random.choice(pool), fade=0.18)
        elif any(word in compact for word in QUESTION_WORDS):
            self._play_first(["think", "tilt", "listen"], fade=0.18)
        elif any(word in compact for word in GREETING_WORDS):
            self._play_first(["wave", "greet", "nod"], fade=0.16)
        elif any(word in compact for word in LAUGH_WORDS):
            self._play_first(["squint", "proud", "nod"], fade=0.14)
        else:
            self._play_first(["listen", "nod", "tilt"], fade=0.2, weight=0.8)

        line = reaction_line(body)
        if line and random.random() < 0.35:
            self.api.after(700, lambda: self.api.say(line, 4200))

    def _on_chat_reply(self, reply: str = "", **_kwargs) -> None:
        if not self.settings.get("reactions", True):
            return
        body = str(reply or "")
        if any(word in body for word in ("不知道", "抱歉", "失败", "错误", "无法")):
            self._play_first(["sweat", "sigh", "tilt"], fade=0.2)
            return
        if len(body) > 120:
            self._play_first(["listen", "nod", "tilt"], fade=0.2, weight=0.7)
        elif "```" in body or "def " in body or "class " in body:
            self._play_first(["think", "tilt", "nod"], fade=0.2, weight=0.8)
        else:
            self._play_first(["nod", "tilt"], fade=0.2, weight=0.8)
        pool = self._available_expressions(["smug", "blush"])
        if pool:
            self._expression(random.choice(pool), hold=2.2, fade=0.4)

    # ---------- 台词与人格 ----------

    def _line_for(self, kind: str, choices) -> str:
        kind = str(kind or "")
        if kind in {
            "idle",
            "pat",
            "poke",
            "sleep",
            "wake",
            "drag",
            "chat_open",
        } or kind.startswith("action:"):
            return pick(kind)
        return ""

    def _prompt(self, query: str = "", viewer: str = "owner", source: str = "pet", channel: str = "private") -> str:
        if not self.settings.get("persona", True):
            return ""
        return PERSONA_PROMPT

    # ---------- 便捷 ----------

    def _play_menu(self, name: str) -> None:
        """右键菜单里主动选择的姿势动作：播完停在最后一帧。"""
        self._play(name, hold=True)

    def _play(
        self,
        name: str,
        *,
        fade: float = 0.2,
        weight: float = 1.0,
        loop: bool | None = None,
        with_host: bool = True,
        hold: bool = False,
    ) -> None:
        if not self.ready or name not in self.clips:
            return
        target = name
        if hold and (name + HOLD_SUFFIX) in self.hold_clips:
            # 主动选择姿势时改播「停在末帧」的变体；后选的姿势会盖住
            # 相同参数，互不冲突的换装状态则可以叠在一起。
            target = name + HOLD_SUFFIX
            loop = False
        if loop is None:
            loop = bool(self.clips[name].get("loop"))
        try:
            self.api.model.play(target, loop=bool(loop), weight=weight, fade=fade, restart=True)
        except Exception as exc:
            self.api.log(f"播放动作 {name} 失败：{exc}")
            return
        self._played_clips.add(target)
        host = HOST_ACTION_FOR_CLIP.get(name) if with_host else None
        if host:
            # 记一下，免得心跳把同一段剪贴又重启一次。
            self._last_action = host
            try:
                self.api.action(host)
            except Exception as exc:
                self.api.log(f"触发宿主动作 {host} 失败：{exc}")

    def _play_first(self, names, **kwargs) -> bool:
        for name in names:
            if name in self.clips:
                self._play(name, **kwargs)
                return True
        return False

    def _available_expressions(self, names) -> list[str]:
        return [name for name in names if name in self.expressions]

    def _expression_first(self, names, **kwargs) -> bool:
        for name in names:
            if name in self.expressions:
                self._expression(name, **kwargs)
                return True
        return False

    def _expression(self, name: str, *, hold: float = 0.0, fade: float = 0.3) -> None:
        if not self.ready or name not in self.expressions:
            return
        try:
            self.api.model.expression(name, hold=hold, fade=fade)
        except Exception as exc:
            self.api.log(f"设置表情 {name} 失败：{exc}")

    def _idle_gap(self) -> float:
        low = max(4.0, float(self.settings.get("idle_min_seconds", 10) or 10))
        high = max(low, float(self.settings.get("idle_max_seconds", 24) or 24))
        return random.uniform(low, high)

    def _chatter_gap(self) -> float:
        low = max(1.0, float(self.settings.get("chatter_min_minutes", 4) or 4)) * 60.0
        high = max(low, float(self.settings.get("chatter_max_minutes", 9) or 9) * 60.0)
        return random.uniform(low, high)

    # ---------- 设置界面用 ----------

    def reload_model(self, path: str | None = None) -> None:
        if path:
            target = Path(path).expanduser()
            self.settings["model_path"] = str(target)
            if target.is_dir():
                self._load(find_model_file(target))
            elif is_archive(target):
                self._extract_async(target)
            else:
                self._load(target)
            return
        self._begin_locate()

    def available_models(self):
        return discover_models(self.settings)

    def available_actions(self) -> list[str]:
        return sorted(self.clips)

    def available_expressions(self) -> list[str]:
        return sorted(self.expressions)

    def preview_action(self, name: str) -> None:
        self._play(str(name), fade=0.15)

    def is_deepseek_model(self) -> bool:
        target = self.model_path or self.settings.get("model_path") or ""
        text = str(target).lower()
        return any(word in text for word in ("deepseek", "大肥鱼", "公皮", "deepseek_chan"))

    def credits(self) -> str:
        if self.is_deepseek_model():
            return (
                "原画及拆分：神宫凉子\n"
                "Live2D 建模：霓虹猫_NeonNeko\n"
                "模型下载：B 站神宫凉子（UID 13737731）的 DeepSeek免费公皮.zip\n"
                "同人二创，非 DeepSeek 官方形象。模型文件仅在本机读取，插件不附带、不上传。"
            )
        return (
            "当前模型：你本机提供的 Live2D 模型\n"
            "插件不附带、不上传任何模型文件，也不会把模型重新分发。\n"
            "深度求索娘皮肤原画及拆分：神宫凉子；Live2D 建模：霓虹猫_NeonNeko。"
        )
