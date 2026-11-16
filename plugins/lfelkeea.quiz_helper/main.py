from __future__ import annotations

"""做题助手：识别屏幕上的题目并让 AI 作答。

触发方式：
- 在聊天框或语音里说「做题」「答题」「这题怎么做」等触发词；
- 在插件设置里点「识别屏幕上的题目」；
- 可选：戳一戳桌宠触发（默认关闭）。

识别方式：
- auto：先用 Windows 自带 OCR 读屏幕文字，读不到足够文字时才把截图交给支持图片的模型；
- text：只把 OCR 文字发给 AI，适合 DeepSeek 等纯文本模型；
- image：把压缩后的截图直接发给支持视觉的模型。

自动填入：
- 默认关闭；打开后答完会有一段倒计时，让你把光标放进答题框；
- 通过 Windows 键盘模拟粘贴或输入选项字母，焦点仍是桌宠或没有切换窗口时会拒绝输入；
- 只在本机执行，不额外联网。

隐私：
- 截图只在内存里压成 JPEG，不写磁盘；
- OCR 文字和答题内容不写日志、不进本地存储，本地只累计答题数量；
- 命中密码、验证码、银行卡等词的行会在发给 AI 前过滤掉。
"""

import ctypes
import os
import re
import time
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

MAX_OCR_CHARS = 3000
DEFAULT_TRIGGERS = ("做题", "答题", "这题怎么做", "拍题", "看题")
NO_TRIGGER_WORDS = ("插件", "设置", "关闭", "关掉", "停用", "怎么用", "不要", "别")
SENSITIVE_LINE_WORDS = (
    "密码",
    "password",
    "passwd",
    "验证码",
    "token",
    "secret",
    "api key",
    "银行卡",
    "身份证",
    "私钥",
)
QUESTION_MARKERS = ("考试", "答题", "作业", "题库", "刷题", "练习", "quiz", "exam", "question")

SYSTEM_PROMPT = (
    "你是一个严谨的做题助手。根据用户给出的屏幕内容（OCR 文字或截图），"
    "找出主人正在做的那一道题并作答。\n"
    "规则：\n"
    "1. 只回答屏幕上明确出现的题目；看不清、没有题目或只是网页导航时，只输出【无题目】。\n"
    "2. 严格按下面的格式输出，不要加其他开头：\n"
    "【题目】题目原文（可以精简，但不要改动数字、单位和选项）\n"
    "【答案】最终答案（选择题写选项字母和内容；填空题写应填内容；计算题写结果）\n"
    "【解析】{detail}\n"
    "3. 不要编造题干、选项或已知条件；不确定时在解析里说明。\n"
    "4. 使用简体中文，答案尽量简短。"
)

DETAILS = {
    "brief": ("简洁", "不超过 100 字的思路"),
    "standard": ("标准", "不超过 250 字的思路和关键步骤"),
    "detailed": ("详细", "不超过 400 字的完整思路和关键步骤"),
}

FILL_MODES = (
    ("粘贴答案全文", "paste"),
    ("只粘贴选项字母", "letter"),
    ("按下选项字母键", "key"),
    ("只复制到剪贴板", "clipboard"),
)
FILL_MODE_VALUES = tuple(value for _, value in FILL_MODES)

VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

DARK_QSS = """
QDialog { background: #16181d; }
QLabel { color: #e8ebf1; }
QLabel#quiz_title { color: #f3f5f9; font-size: 17px; font-weight: 600; }
QLabel#quiz_muted { color: #98a2b3; font-size: 12px; }
QLineEdit, QComboBox, QSpinBox {
    background: #1e2128; color: #e8ebf1; border: 1px solid #2d323d;
    border-radius: 6px; padding: 6px 8px; min-height: 20px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #5b8cff; }
QComboBox QAbstractItemView { background: #1e2128; color: #e8ebf1; selection-background-color: #33405c; }
QCheckBox { color: #d7dce5; spacing: 8px; }
QPlainTextEdit {
    background: #1e2128; color: #e8ebf1; border: 1px solid #2d323d;
    border-radius: 8px; padding: 10px; font-size: 13px;
}
QPushButton {
    background: #252a33; color: #e8ebf1; border: 1px solid #343b47;
    border-radius: 6px; padding: 7px 14px;
}
QPushButton:hover { background: #2f3540; border-color: #465063; }
QPushButton#quiz_primary {
    background: #5b8cff; border-color: #5b8cff; color: #ffffff; font-weight: 600;
}
QPushButton#quiz_primary:hover { background: #6f9bff; }
"""

_state: dict[str, Any] = {
    "active": False,
    "busy": False,
    "seq": 0,
    "stage": "",
    "ocr_failed": False,
    "ocr": None,
    "frame": None,
    "window": None,
    "trigger_hwnd": 0,
    "fill": 0,
}


# ---------- 设置 ----------


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "开", "是"):
            return True
        if text in ("0", "false", "no", "off", "关", "否"):
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _options(api) -> dict[str, Any]:
    try:
        raw = api.settings or {}
    except Exception:
        raw = {}
    triggers_text = str(raw.get("trigger_words") or "")
    triggers = tuple(
        word.strip() for word in re.split(r"[,，、;；\s]+", triggers_text) if word.strip()
    )
    if not triggers:
        triggers = DEFAULT_TRIGGERS

    mode = str(raw.get("recognition") or "auto").strip().lower()
    if mode not in ("auto", "text", "image"):
        mode = "auto"
    detail = str(raw.get("detail") or "standard").strip().lower()
    if detail not in DETAILS:
        detail = "standard"

    fill_mode = str(raw.get("fill_mode") or "paste").strip().lower()
    if fill_mode not in FILL_MODE_VALUES:
        fill_mode = "paste"
    try:
        fill_delay = int(raw.get("fill_delay", 3))
    except Exception:
        fill_delay = 3
    fill_delay = max(1, min(15, fill_delay))

    return {
        "enabled": _as_bool(raw.get("enable_chat_trigger"), True),
        "triggers": triggers,
        "recognition": mode,
        "detail": detail,
        "show_window": _as_bool(raw.get("show_window"), True),
        "poke_trigger": _as_bool(raw.get("poke_trigger"), False),
        "extra": str(raw.get("extra") or "").strip()[:200],
        "auto_fill": _as_bool(raw.get("auto_fill"), False),
        "fill_mode": fill_mode,
        "fill_delay": fill_delay,
        "fill_enter": _as_bool(raw.get("fill_enter"), False),
    }


# ---------- 生命周期 ----------


def setup(api) -> None:
    _state.update(
        {
            "active": True,
            "busy": False,
            "seq": 0,
            "stage": "",
            "ocr_failed": False,
            "ocr": None,
            "frame": None,
            "window": None,
            "trigger_hwnd": 0,
            "fill": 0,
        }
    )

    def on_user(text: str = "", **_: Any) -> None:
        _on_user(api, text)

    def on_poke(**_: Any) -> None:
        if _options(api)["poke_trigger"]:
            _begin(api, "poke")

    def on_screen(context: Any = None, **_: Any) -> None:
        _annotate(context)

    def context_provider(query: str = "", **_: Any) -> str:
        opts = _options(api)
        if not opts["enabled"]:
            return ""
        lowered = str(query or "").casefold()
        if not any(word.casefold() in lowered for word in opts["triggers"]):
            return ""
        return (
            "【做题助手】主人正在让你识别屏幕上的题目，插件会读取屏幕并给出答案。"
            "你不必猜题目，也不要给出自己的答案，简短回应一句就好。"
        )

    api.on("chat.user", on_user)
    api.on("pet.poke", on_poke)
    api.on("screen.context", on_screen)
    register = getattr(api, "register_prompt_provider", None)
    if callable(register):
        register(context_provider)
    add_menu = getattr(api, "add_menu_item", None)
    if callable(add_menu):
        try:
            add_menu("识别屏幕上的题目", lambda: _begin(api, "menu"))
        except Exception as exc:
            api.log(f"注册右键菜单失败：{str(exc)[:120]}")
    api.log("做题助手已启动：聊天里说「做题」、右键「识别屏幕上的题目」或到插件设置里手动识别")


def teardown() -> None:
    _state["active"] = False
    _state["busy"] = False
    _state["seq"] = int(_state.get("seq") or 0) + 1
    _state["stage"] = ""
    _state["ocr_failed"] = False
    _cancel_fill()

    task = _state.get("ocr")
    _state["ocr"] = None
    if task is not None:
        try:
            task.cancel()
        except Exception:
            pass

    _drop_window()
    _state["frame"] = None


def open_settings(api, parent=None):
    return QuizSettingsDialog(api, parent)


# ---------- 触发 ----------


def _on_user(api, text: str) -> None:
    content = str(text or "").strip()
    if not content or len(content) > 60:
        return
    opts = _options(api)
    if not opts["enabled"]:
        return
    if any(word in content for word in NO_TRIGGER_WORDS):
        return
    lowered = content.casefold()
    if not any(word.casefold() in lowered for word in opts["triggers"]):
        return
    _begin(api, "chat")


def _annotate(context: Any) -> None:
    if context is None:
        return
    try:
        blob = f"{getattr(context, 'title', '')} {getattr(context, 'process', '')}".casefold()
    except Exception:
        return
    if any(word.casefold() in blob for word in QUESTION_MARKERS):
        try:
            context.notes.append("主人可能在做题，需要时可以提醒他说「做题」让插件识别题目")
        except Exception:
            pass


def _begin(api, source: str, restore=None) -> None:
    if not _state["active"]:
        return
    if _state["busy"]:
        api.say("上一道题还在看，等我一下。", 3000)
        return

    _cancel_fill()

    # 记下触发时最前面的窗口；填答案时如果还是它，说明主人没切到答题框。
    _state["trigger_hwnd"] = _foreground_hwnd()

    # 先占住忙碌状态，避免 300ms 延迟里被第二个触发词重复截屏。
    _state["busy"] = True
    _hide_window()
    if source != "chat":
        api.say("我看看屏幕上的题目。", 2500)

    def capture() -> None:
        _capture(api, restore)

    _schedule(300, capture)


def _capture(api, restore=None) -> None:
    if not _state["active"]:
        return
    _state["busy"] = True
    _state["seq"] = int(_state.get("seq") or 0) + 1
    _state["stage"] = "capture"
    _state["ocr_failed"] = False
    seq = int(_state["seq"])
    opts = _options(api)

    try:
        frame = api.capture_screen()
    except Exception as exc:
        _restore_view(restore)
        _fail(api, f"截屏失败：{exc}", seq)
        return

    _restore_view(restore)

    if frame is None or not getattr(frame, "image_jpeg", b""):
        _fail(api, "屏幕没截到，可能被系统挡住了。先把题目窗口切到最前面再试。", seq)
        return

    _state["frame"] = frame

    if opts["recognition"] in ("auto", "text"):
        task = _make_ocr(frame)
        if task is not None:
            _state["ocr"] = task
            _state["stage"] = "ocr"
            try:
                task.signals.done.connect(
                    lambda text, error: _after_ocr(api, seq, text, error)
                )
                task.start()
            except Exception as exc:
                _state["ocr"] = None
                api.log(f"OCR 启动失败：{str(exc)[:160]}")
            else:
                # OcrTask 自己有 20 秒超时，这里再兜一层，避免回调丢失把插件卡在忙碌状态。
                _schedule(23000, lambda: _ocr_timeout(api, seq))
                return

    _ask_vision(api, seq, frame, opts)


def _make_ocr(frame):
    factory = _ocr_factory()
    if factory is None:
        return None
    try:
        return factory(frame.image_jpeg)
    except Exception:
        return None


def _ocr_factory():
    """桌宠自带 Windows OCR；拿不到时退回图片理解。"""
    try:
        from screen_watcher import OcrTask

        return OcrTask
    except Exception:
        return None


def _ocr_timeout(api, seq: int) -> None:
    if not _alive(seq) or _state.get("stage") != "ocr":
        return
    _state["ocr"] = None
    opts = _options(api)
    frame = _state.get("frame")
    api.log("OCR 超时，未返回识别结果")
    if opts["recognition"] == "text" or frame is None:
        _fail(api, "读屏幕文字超时了。可以再试一次，或把识别方式改成「图片」。", seq)
        return
    _state["ocr_failed"] = True
    _ask_vision(api, seq, frame, opts)


def _after_ocr(api, seq: int, text: str, error: str) -> None:
    if not _alive(seq) or _state.get("stage") != "ocr":
        return
    _state["ocr"] = None
    cleaned = _clean_ocr(text)
    if cleaned and len(cleaned) >= 6:
        _ask_text(api, seq, cleaned, _options(api))
        return

    if error:
        api.log(f"OCR 未识别：{str(error)[:160]}")
    opts = _options(api)
    frame = _state.get("frame")
    if opts["recognition"] == "text" or frame is None:
        _fail(
            api,
            "屏幕上的字没读出来。把题目窗口放大一点再试，或到设置里改用「图片」识别。",
            seq,
        )
        return
    _state["ocr_failed"] = True
    _ask_vision(api, seq, frame, opts)


def _clean_ocr(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.casefold()
        if any(word in lowered for word in SENSITIVE_LINE_WORDS):
            continue
        lines.append(stripped)
    return "\n".join(lines)[:MAX_OCR_CHARS].strip()


# ---------- 自动填入 ----------

_CHOICE_HEAD_RE = re.compile(
    r"^[\s（(\[【]*([A-Ha-h])(?=$|[\s.．、,，:：)）\]】])"
)
_CHOICE_KEYWORD_RE = re.compile(
    r"(?:答案|选项|应选|选择|选)\s*(?:是|为|[:：])?\s*[（(\[【]*\s*"
    r"([A-Ha-h])(?=$|[\s.．、,，:：)）\]】])"
)


def _user32():
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
    except Exception:
        return None
    try:
        # 64 位系统上 HWND 是指针宽度，默认 c_int 返回值会被截断。
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        user32.keybd_event.argtypes = [
            ctypes.c_ubyte,
            ctypes.c_ubyte,
            ctypes.c_uint,
            ctypes.c_void_p,
        ]
    except Exception:
        pass
    return user32


def _foreground_hwnd() -> int:
    user32 = _user32()
    if user32 is None:
        return 0
    try:
        return int(user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _foreground_unchanged() -> bool:
    trigger = int(_state.get("trigger_hwnd") or 0)
    if not trigger:
        return False
    return _foreground_hwnd() == trigger


def _foreground_owned() -> bool:
    """当前最前面的窗口是不是桌宠自己。"""
    hwnd = _foreground_hwnd()
    user32 = _user32()
    if not hwnd or user32 is None:
        return False
    try:
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except Exception:
        return False


def _set_clipboard(text: str) -> bool:
    try:
        app = QApplication.instance()
        if app is None:
            return False
        clipboard = app.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(str(text))
        return True
    except Exception:
        return False


def _tap_key(vk: int) -> None:
    user32 = _user32()
    if user32 is None:
        return
    try:
        user32.keybd_event(int(vk), 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(int(vk), 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def _paste_clipboard() -> None:
    user32 = _user32()
    if user32 is None:
        return
    try:
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, 0, 0, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass


def _choice_letter(answer: str) -> str:
    """从答案里找出选项字母 A–H；没有就返回空串。"""
    text = str(answer or "").strip()
    if not text:
        return ""
    match = _CHOICE_HEAD_RE.match(text)
    if match:
        return match.group(1).upper()
    match = _CHOICE_KEYWORD_RE.search(text)
    if match:
        return match.group(1).upper()
    return ""


def _fill_payload(answer: str, mode: str) -> tuple[str, str]:
    """返回 (要输入的文字, 输入方式)，输入方式为 paste / key / clipboard。"""
    text = str(answer or "").strip()
    if mode == "clipboard":
        return text, "clipboard"
    if mode in ("letter", "key"):
        letter = _choice_letter(text)
        if letter:
            return letter, "key" if mode == "key" else "paste"
    return text, "paste"


def _cancel_fill() -> None:
    _state["fill"] = int(_state.get("fill") or 0) + 1


def _schedule_fill(api, text: str, action: str, press_enter: bool, seconds: int) -> int:
    token = int(_state.get("fill") or 0) + 1
    _state["fill"] = token
    delay = max(1, min(15, int(seconds)))
    _schedule(delay * 1000, lambda: _do_fill(api, token, text, action, press_enter))
    return token


def _do_fill(api, token: int, text: str, action: str, press_enter: bool) -> None:
    if int(_state.get("fill") or 0) != token or not _state["active"]:
        return
    if _user32() is None:
        api.say("自动填入目前只支持 Windows，答案可以在解析窗口里复制。", 5000)
        return

    copied = _set_clipboard(text)
    if _foreground_owned() or _foreground_unchanged():
        if copied:
            api.say(
                "答题框还没选中，答案先放在剪贴板了，点一下输入框按 Ctrl+V 就行。",
                7000,
            )
        else:
            api.say("答题框还没选中，先点一下输入框再试一次。", 6000)
        return

    if action == "clipboard":
        if copied:
            api.say("答案已复制到剪贴板，按 Ctrl+V 粘贴。", 5000)
        else:
            api.say("复制答案失败，手动复制一下吧。", 5000)
        return

    if action == "key" and len(text) == 1 and text.isalpha():
        _tap_key(ord(text.upper()))
    else:
        if not copied:
            api.say("复制答案失败，没能填进去。", 5000)
            return
        _paste_clipboard()

    if press_enter:
        _schedule(150, lambda: _tap_key(VK_RETURN))
        api.say("已经填好并按下回车，记得核对。", 5000)
    else:
        api.say("已经填进答题框了，核对后自己提交。", 5000)


def _manual_fill(api, answer: str) -> None:
    opts = _options(api)
    text, action = _fill_payload(answer, opts["fill_mode"])
    delay = int(opts["fill_delay"])
    _schedule_fill(api, text, action, opts["fill_enter"], delay)
    api.say(f"{delay} 秒后填入答案，快把光标放到答题框里。", delay * 1000 + 2000)


# ---------- 请求 AI ----------


def _system_prompt(opts: dict[str, Any]) -> str:
    prompt = SYSTEM_PROMPT.format(detail=DETAILS[opts["detail"]][1])
    if opts["extra"]:
        prompt += f"\n5. 主人的额外要求：{opts['extra']}"
    return prompt


def _ask_text(api, seq: int, text: str, opts: dict[str, Any]) -> None:
    messages = [
        {"role": "system", "content": _system_prompt(opts)},
        {
            "role": "user",
            "content": (
                "以下是屏幕 OCR 文本（可能夹杂网页导航等无关内容）：\n"
                f"{text}\n\n请找出主人正在做的那一道题并作答。"
            ),
        },
    ]
    _send(api, seq, messages, vision=False)


def _ask_vision(api, seq: int, frame, opts: dict[str, Any]) -> None:
    try:
        data_uri = frame.data_uri()
    except Exception:
        data_uri = ""
    if not data_uri:
        _fail(api, "截图没有生成出来，再试一次吧。", seq)
        return

    messages = [
        {"role": "system", "content": _system_prompt(opts)},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "这是当前屏幕截图，请找出主人正在做的题目并作答。"},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        },
    ]
    _send(api, seq, messages, vision=True)


def _send(api, seq: int, messages: list[dict[str, Any]], vision: bool) -> None:
    _state["stage"] = "ai"

    def callback(reply, error) -> None:
        _on_reply(api, seq, reply, error, vision)

    try:
        api.chat(messages, callback)
    except Exception as exc:
        _fail(api, f"请求 AI 失败：{str(exc)[:120]}", seq)


def _on_reply(api, seq: int, reply, error, vision: bool) -> None:
    if not _alive(seq):
        return

    if error:
        message = str(error or "")
        if vision and _looks_like_rejection(message):
            if _state.get("ocr_failed"):
                _fail(
                    api,
                    "屏幕文字没读出来，当前模型也不支持图片。可以在 Windows 设置里安装中文 OCR 语言包，或换一个支持图片的模型。",
                    seq,
                )
            else:
                _fail(
                    api,
                    "当前模型看不了图片。把识别方式改成「文字（OCR）」，或者换一个支持图片的模型。",
                    seq,
                )
        else:
            _fail(api, f"AI 没给出结果：{message[:100]}", seq)
        return

    text = _clean_reply(reply)
    if not text:
        _fail(api, "AI 返回了空回复，再试一次吧。", seq)
        return

    if _is_no_question(text):
        _finish(api, "屏幕上没看到明确的题目。先把题目窗口切到最前面再试一次。")
        return

    answer = _section(text, "答案") or _first_line(text)
    if not answer:
        _fail(api, "识别到了内容，但没提取出答案。可以到设置里把解析改详细一点再试。", seq)
        return

    _bump_count(api)
    opts = _options(api)
    say = f"答案：{answer}"

    if opts["auto_fill"] and _user32() is not None:
        fill_text, action = _fill_payload(answer, opts["fill_mode"])
        delay = int(opts["fill_delay"])
        token = _schedule_fill(api, fill_text, action, opts["fill_enter"], delay)
        say += f"。{delay} 秒后自动填入，快把光标放到答题框里"
        if opts["show_window"]:
            # 等填完再弹解析窗口，避免窗口抢走焦点。
            _schedule(
                delay * 1000 + 800,
                lambda: _show_result_later(api, text, answer, token),
            )
    elif opts["show_window"]:
        if _show_result(api, text, answer):
            say += "。完整解析我放在窗口里了。"

    api.say(say, max(10000, int(opts["fill_delay"]) * 1000 + 3000))
    try:
        api.action("nod")
    except Exception:
        pass
    _finish(api, None)


def _looks_like_rejection(error: str) -> bool:
    try:
        from ai_client import looks_like_vision_rejection

        return looks_like_vision_rejection(error)
    except Exception:
        text = str(error or "").casefold()
        return any(
            key in text
            for key in (
                "image",
                "vision",
                "multimodal",
                "content must be a string",
                "invalid content",
                "image_url",
            )
        )


# ---------- 结果解析 ----------


def _clean_reply(text: Any) -> str:
    value = str(text or "").strip()
    return re.sub(r"\n{3,}", "\n\n", value)


def _is_no_question(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return "【无题目】" in compact or compact in ("无题目", "没有题目")


def _section(text: str, name: str) -> str:
    match = re.search(
        rf"【\s*{re.escape(name)}\s*】\s*(.*?)(?=\n?\s*【|\Z)", text, re.S
    )
    if not match:
        return ""
    return " ".join(match.group(1).split())


def _first_line(text: str) -> str:
    for line in text.splitlines():
        value = " ".join(line.split())
        if value:
            return value[:160]
    return ""


# ---------- 状态 ----------


def _alive(seq: int) -> bool:
    return bool(_state["active"] and _state["busy"] and int(_state["seq"]) == int(seq))


def _finish(api, message: str | None) -> None:
    _state["busy"] = False
    _state["seq"] = int(_state.get("seq") or 0) + 1
    _state["stage"] = ""
    _state["ocr_failed"] = False
    _state["ocr"] = None
    _state["frame"] = None
    if message:
        api.say(message, 6000)


def _fail(api, message: str, seq: int | None) -> None:
    if seq is not None and not _alive(seq):
        return
    api.log(str(message)[:200])
    _finish(api, message)


def _bump_count(api) -> int:
    try:
        store = api.storage
        store["answered"] = int(store.get("answered", 0) or 0) + 1
        store["last_at"] = time.strftime("%Y-%m-%d %H:%M")
        api.save_storage()
        return int(store["answered"])
    except Exception:
        return 0


def _schedule(delay_ms: int, callback) -> None:
    QTimer.singleShot(max(0, int(delay_ms)), callback)


def _restore_view(restore) -> None:
    if not callable(restore):
        return
    try:
        restore()
    except Exception:
        pass


# ---------- 窗口 ----------


def _hide_window() -> None:
    window = _state.get("window")
    if window is None:
        return
    try:
        window.hide()
    except Exception:
        pass


def _drop_window() -> None:
    window = _state.get("window")
    _state["window"] = None
    if window is None:
        return
    try:
        window.close()
        window.deleteLater()
    except Exception:
        pass


def _forget_window(window) -> None:
    if _state.get("window") is window:
        _state["window"] = None


def _show_result_later(api, text: str, answer: str, token: int) -> None:
    if int(_state.get("fill") or 0) != token or not _state["active"]:
        return
    _show_result(api, text, answer)


def _show_result(api, text: str, answer: str = "") -> bool:
    if QApplication.instance() is None:
        return False
    _drop_window()
    supported = _user32() is not None
    try:
        window = QuizResultDialog(
            text,
            on_again=lambda: _begin(api, "manual"),
            on_fill=(lambda: _manual_fill(api, answer)) if answer else None,
            fill_supported=supported,
        )
    except Exception as exc:
        api.log(f"解析窗口打开失败：{str(exc)[:160]}")
        return False

    _state["window"] = window
    try:
        window.destroyed.connect(lambda *_, w=window: _forget_window(w))
        window.show()
        window.raise_()
        window.activateWindow()
    except Exception as exc:
        api.log(f"解析窗口显示失败：{str(exc)[:160]}")
        _state["window"] = None
        return False
    return True


class QuizResultDialog(QDialog):
    def __init__(
        self,
        text: str,
        on_again=None,
        on_fill=None,
        fill_supported: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._on_again = on_again
        self._on_fill = on_fill
        self.setWindowTitle("做题助手 · 解析")
        self.setMinimumSize(640, 480)
        self.resize(760, 580)
        self.setStyleSheet(DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        title = QLabel("做题助手")
        title.setObjectName("quiz_title")
        layout.addWidget(title)

        hint = QLabel("结果只根据当前屏幕识别；题干、选项或图片有缺失时，答案可能不准。")
        hint.setObjectName("quiz_muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(text)
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self.editor, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.again_btn = QPushButton("再答一题")
        self.fill_btn = QPushButton("填入答案" if fill_supported else "填入答案（仅 Windows）")
        self.fill_btn.setEnabled(bool(fill_supported and callable(on_fill)))
        if fill_supported:
            self.fill_btn.setToolTip("窗口会先隐藏并倒计时，把光标放到答题框里就会自动填入")
        self.copy_btn = QPushButton("复制解析")
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("quiz_primary")

        self.again_btn.clicked.connect(self._again)
        self.fill_btn.clicked.connect(self._fill)
        self.copy_btn.clicked.connect(self._copy)
        close_btn.clicked.connect(self.close)

        row.addWidget(self.again_btn)
        row.addWidget(self.fill_btn)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _again(self) -> None:
        self.hide()
        if callable(self._on_again):
            self._on_again()

    def _fill(self) -> None:
        if not callable(self._on_fill):
            return
        self.hide()
        self._on_fill()

    def _copy(self) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(self._text)
        self.copy_btn.setText("已复制")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("复制解析"))


class QuizSettingsDialog(QDialog):
    MODES = (
        ("自动（先 OCR，读不到再看图）", "auto"),
        ("文字（只发 OCR 结果）", "text"),
        ("图片（发给视觉模型）", "image"),
    )
    DETAIL_ITEMS = (
        ("简洁", "brief"),
        ("标准", "standard"),
        ("详细", "detailed"),
    )

    def __init__(self, api, parent=None) -> None:
        super().__init__(parent)
        self._api = api
        self.setWindowTitle("做题助手 · 设置")
        self.setMinimumSize(580, 540)
        self.resize(640, 590)
        self.setStyleSheet(DARK_QSS)

        opts = _options(api)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)

        title = QLabel("做题助手")
        title.setObjectName("quiz_title")
        layout.addWidget(title)

        desc = QLabel(
            "截图只在本机压缩后交给当前配置的 AI，不会写进磁盘。"
            "按「识别屏幕上的题目」前，先把题目窗口切到最前面。"
            "自动填入通过 Windows 键盘模拟完成，只在本机执行。"
        )
        desc.setObjectName("quiz_muted")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self.chat_check = QCheckBox("在聊天里说触发词时自动识别屏幕")
        self.chat_check.setChecked(opts["enabled"])
        layout.addWidget(self.chat_check)

        layout.addWidget(QLabel("触发词（用逗号分隔）"))
        self.words_edit = QLineEdit("，".join(opts["triggers"]))
        self.words_edit.setPlaceholderText("做题，答题，这题怎么做")
        layout.addWidget(self.words_edit)

        layout.addWidget(QLabel("识别方式"))
        self.mode_combo = QComboBox()
        for label, value in self.MODES:
            self.mode_combo.addItem(label, value)
        self._select(self.mode_combo, opts["recognition"])
        layout.addWidget(self.mode_combo)

        layout.addWidget(QLabel("解析长度"))
        self.detail_combo = QComboBox()
        for label, value in self.DETAIL_ITEMS:
            self.detail_combo.addItem(label, value)
        self._select(self.detail_combo, opts["detail"])
        layout.addWidget(self.detail_combo)

        self.window_check = QCheckBox("答完后弹出完整解析窗口")
        self.window_check.setChecked(opts["show_window"])
        layout.addWidget(self.window_check)

        self.poke_check = QCheckBox("戳一戳桌宠也识别题目")
        self.poke_check.setChecked(opts["poke_trigger"])
        layout.addWidget(self.poke_check)

        self.auto_fill_check = QCheckBox("答完后自动填入答案（Windows）")
        self.auto_fill_check.setChecked(opts["auto_fill"])
        layout.addWidget(self.auto_fill_check)

        fill_row = QHBoxLayout()
        fill_row.setSpacing(8)
        fill_row.addWidget(QLabel("填入方式"))
        self.fill_mode_combo = QComboBox()
        for label, value in FILL_MODES:
            self.fill_mode_combo.addItem(label, value)
        self._select(self.fill_mode_combo, opts["fill_mode"])
        fill_row.addWidget(self.fill_mode_combo, 1)
        fill_row.addWidget(QLabel("倒计时"))
        self.fill_delay_spin = QSpinBox()
        self.fill_delay_spin.setRange(1, 15)
        self.fill_delay_spin.setSuffix(" 秒")
        self.fill_delay_spin.setValue(opts["fill_delay"])
        fill_row.addWidget(self.fill_delay_spin)
        layout.addLayout(fill_row)

        self.fill_enter_check = QCheckBox("填入后自动按回车（可能直接提交，谨慎开启）")
        self.fill_enter_check.setChecked(opts["fill_enter"])
        layout.addWidget(self.fill_enter_check)

        layout.addWidget(QLabel("额外要求（可选）"))
        self.extra_edit = QLineEdit(opts["extra"])
        self.extra_edit.setPlaceholderText("例如：只给答案，不要解析")
        layout.addWidget(self.extra_edit)

        self.status = QLabel("")
        self.status.setObjectName("quiz_muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        capture_btn = QPushButton("识别屏幕上的题目")
        capture_btn.setObjectName("quiz_primary")
        save_btn = QPushButton("保存")
        close_btn = QPushButton("关闭")
        capture_btn.clicked.connect(self._capture_now)
        save_btn.clicked.connect(self._save)
        close_btn.clicked.connect(self.close)
        row.addWidget(capture_btn)
        row.addWidget(save_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)

        self._refresh_status()

    @staticmethod
    def _select(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _refresh_status(self) -> None:
        try:
            count = int(self._api.storage.get("answered", 0) or 0)
        except Exception:
            count = 0
        try:
            last_at = str(self._api.storage.get("last_at") or "")
        except Exception:
            last_at = ""
        suffix = f"，上次 {last_at}" if last_at else ""
        self.status.setText(f"已作答 {count} 题（只在本机计数{suffix}）")

    def _save(self) -> None:
        values = {
            "enable_chat_trigger": self.chat_check.isChecked(),
            "trigger_words": self.words_edit.text().strip(),
            "recognition": self.mode_combo.currentData(),
            "detail": self.detail_combo.currentData(),
            "show_window": self.window_check.isChecked(),
            "poke_trigger": self.poke_check.isChecked(),
            "auto_fill": self.auto_fill_check.isChecked(),
            "fill_mode": self.fill_mode_combo.currentData(),
            "fill_delay": int(self.fill_delay_spin.value()),
            "fill_enter": self.fill_enter_check.isChecked(),
            "extra": self.extra_edit.text().strip(),
        }
        try:
            self._api.save_settings(values)
        except Exception as exc:
            self.status.setText(f"保存失败：{exc}")
            return
        self._refresh_status()
        self.status.setText("已保存，立即生效。")
        try:
            self._api.say("做题助手设置已保存。", 3000)
        except Exception:
            pass

    def _capture_now(self) -> None:
        self.hide()
        if _options(self._api)["auto_fill"] and _user32() is not None:
            # 自动填入时不再把设置窗口弹回来，免得倒计时结束时它抢着焦点。
            _begin(self._api, "manual")
            return

        def restore() -> None:
            try:
                self.show()
                self.raise_()
                self.activateWindow()
            except Exception:
                pass

        _begin(self._api, "manual", restore=restore)
