"""桌宠的深度求索娘服务：模型、动作、反应和台词。"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime
from pathlib import Path

from .clips import action_for_host, build_clips
from .lines import PERSONA_PROMPT, greeting_for_hour, pick, reaction_line
from .model_source import extract_zip, find_model_file, locate

IDLE_GESTURES = [
    "tilt",
    "glance",
    "sigh",
    "listen",
    "nod",
    "shake",
    "tail_hide",
    "tail_show",
    "proud",
    "wave",
    "stretch",
]
CUTE_GESTURES = ["shy", "love", "squint", "pout", "dance", "sing"]
SPECIAL_GESTURES = ["dress", "chibi", "special", "foxtail_out", "point_out"]

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
        self._pat_count = int(self.state.get("pat_count") or 0)
        self._poke_count = int(self.state.get("poke_count") or 0)
        self._stop_event = threading.Event()
        self._prompt_handle = None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._started:
            return
        self._started = True

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
            self.api.model.unload()
        except Exception:
            pass
        try:
            self.api.save_storage()
        except Exception:
            pass

    # ---------- 找模型 ----------

    def _begin_locate(self) -> None:
        location = locate(self.settings)
        if location is None:
            self.api.log("没有找到大肥鱼模型")
            self.api.after(2600, lambda: self.api.say(pick("no_model"), 7000))
            return
        if location.ready:
            self._load(location.model)
            return
        if location.zip_path is not None:
            self._extract_async(location.zip_path)
            return
        if location.directory is not None:
            model = find_model_file(location.directory)
            if model is not None:
                self._load(model)
                return
        self.api.after(2600, lambda: self.api.say(pick("no_model"), 7000))

    def _extract_async(self, zip_path: Path) -> None:
        self.api.log(f"从压缩包准备模型：{zip_path}")
        self.api.after(
            600,
            lambda: self.api.say("第一次用这个皮肤，我先从压缩包里把大肥鱼搬出来……", 5200),
        )
        self._thread_error = ""
        self._thread_result = None

        def worker() -> None:
            try:
                result = extract_zip(zip_path, self.data_dir)
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
            "模型解包失败了，可能是压缩包不完整。去 B 站神宫凉子（UID 13737731）重新下一份，再到设置里手动选。",
            8000,
        )

    def _load(self, model_path: Path | None) -> None:
        if model_path is None:
            return
        model_path = Path(model_path)
        if not model_path.is_file():
            self.api.say(pick("no_model"), 7000)
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
            self.api.model.load(
                str(model_path),
                height=int(self.settings.get("height", 760) or 760),
                fps=int(self.settings.get("fps", 30) or 30),
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
        model_dir = self.model_path.parent if self.model_path is not None else None
        try:
            self.clips, self.expressions = build_clips(model_dir) if model_dir else ({}, {})
            self.api.model.define_clips(self.clips)
            if self.expressions:
                self.api.model.define_expressions(self.expressions)
            self.api.model.follow_cursor(bool(self.settings.get("gaze_follow", True)))
            self.api.log(f"模型就绪：{len(self.clips)} 个动作，{len(self.expressions)} 个表情")
        except Exception as exc:
            self.api.log(f"定义动作失败：{exc}")

        if self.settings.get("persona", True):
            try:
                self.api.say(greeting_for_hour(datetime.now().hour), 4600)
            except Exception:
                pass
        self._play("greet", fade=0.2, weight=0.9)
        if self._should_show_credit():
            self.api.after(5200, lambda: self.api.say(pick("credit"), 9000))
            self.settings["show_credit_on_first_run"] = False
            try:
                self.api.save_settings({"show_credit_on_first_run": False})
            except Exception:
                pass

    def _should_show_credit(self) -> bool:
        return bool(self.settings.get("show_credit_on_first_run", True))

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
                self._play(clip, fade=0.14)

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
            gesture = random.choice(SPECIAL_GESTURES)
        elif roll < 0.28:
            gesture = random.choice(CUTE_GESTURES)
        else:
            gesture = random.choice(IDLE_GESTURES)
        self._play(gesture, fade=0.22, weight=0.85)

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
            self._play("love", fade=0.25)
            self._expression("heart", hold=4.0)
        elif random.random() < 0.3:
            self._play("squint", fade=0.18)
        else:
            self._play("shy", fade=0.18)

    def _on_poke(self) -> None:
        self._poke_count += 1
        self.state["poke_count"] = self._poke_count
        try:
            self.api.save_storage()
        except Exception:
            pass
        if not self.settings.get("reactions", True):
            return
        gesture = random.choice(["surprise", "pout", "squint", "deadpan", "dizzy"])
        self._play(gesture, fade=0.12)

    def _on_chat_user(self, text: str = "", **_kwargs) -> None:
        if not self.settings.get("reactions", True):
            return
        body = str(text or "")
        compact = body.lower()
        if any(word in compact for word in PRAISE_WORDS):
            self._play("shy", fade=0.2)
            self._expression("blush", hold=5.0)
        elif any(word in compact for word in SCOLD_WORDS):
            self._play(random.choice(["pout", "deadpan"]), fade=0.18)
        elif any(word in compact for word in QUESTION_WORDS):
            self._play("think", fade=0.18)
        elif any(word in compact for word in GREETING_WORDS):
            self._play("wave", fade=0.16)
        elif any(word in compact for word in LAUGH_WORDS):
            self._play("squint", fade=0.14)
        else:
            self._play("listen", fade=0.2, weight=0.8)

        line = reaction_line(body)
        if line and random.random() < 0.35:
            self.api.after(700, lambda: self.api.say(line, 4200))

    def _on_chat_reply(self, reply: str = "", **_kwargs) -> None:
        if not self.settings.get("reactions", True):
            return
        body = str(reply or "")
        if any(word in body for word in ("不知道", "抱歉", "失败", "错误", "无法")):
            self._play("sweat", fade=0.2)
            return
        if len(body) > 120:
            self._play("listen", fade=0.2, weight=0.7)
        elif "```" in body or "def " in body or "class " in body:
            self._play("think", fade=0.2, weight=0.8)
        else:
            self._play("nod", fade=0.2, weight=0.8)
        self._expression(random.choice(["smug", "blush"]), hold=2.2, fade=0.4)

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

    def _play(self, name: str, *, fade: float = 0.2, weight: float = 1.0, loop: bool = False) -> None:
        if not self.ready or name not in self.clips:
            return
        try:
            self.api.model.play(name, loop=loop, weight=weight, fade=fade, restart=True)
        except Exception as exc:
            self.api.log(f"播放动作 {name} 失败：{exc}")

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
            target = Path(path)
            if target.suffix.lower() == ".zip":
                self._extract_async(target)
            elif target.is_dir():
                self._load(find_model_file(target))
            else:
                self._load(target)
            return
        self._begin_locate()

    def available_actions(self) -> list[str]:
        return sorted(self.clips)

    def available_expressions(self) -> list[str]:
        return sorted(self.expressions)

    def preview_action(self, name: str) -> None:
        self._play(str(name), fade=0.15)

    def credits(self) -> str:
        return (
            "原画及拆分：神宫凉子\n"
            "Live2D 建模：霓虹猫_NeonNeko\n"
            "模型下载：B 站神宫凉子（UID 13737731）的 DeepSeek免费公皮.zip\n"
            "同人二创，非 DeepSeek 官方形象。模型文件仅在本机读取，插件不附带、不上传。"
        )
