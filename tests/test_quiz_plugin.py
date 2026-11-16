from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.quiz_helper" / "main.py"


class FakeSignal:
    def __init__(self) -> None:
        self.slots = []

    def connect(self, slot) -> None:
        self.slots.append(slot)

    def emit(self, *args) -> None:
        for slot in list(self.slots):
            slot(*args)


class FakeOcrTask:
    last = None

    def __init__(self, image_jpeg: bytes) -> None:
        self.image_jpeg = image_jpeg
        self.signals = SimpleNamespace(done=FakeSignal())
        self.cancelled = False
        FakeOcrTask.last = self

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        self.cancelled = True


class FakeFrame:
    image_jpeg = b"fake-jpeg"
    width = 1280
    height = 720

    def data_uri(self) -> str:
        return "data:image/jpeg;base64,ZmFrZS1qcGVn"


class FakeAPI:
    version = 1

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = dict(settings or {})
        self.said: list[str] = []
        self.actions: list[str] = []
        self.logs: list[str] = []
        self.storage: dict = {}
        self.saved = 0
        self.saved_settings: dict = {}
        self.handlers: dict[str, list] = {}
        self.captures = 0
        self.chats: list[list[dict]] = []
        self.callback = None
        self.frame = FakeFrame()
        self.prompt_provider = None

    def register_prompt_provider(self, callback):
        self.prompt_provider = callback
        return callback

    def say(self, text: str, duration: int = 5000) -> None:
        self.said.append(text)

    def action(self, name: str) -> None:
        self.actions.append(name)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def on(self, event: str, callback) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def capture_screen(self):
        self.captures += 1
        return self.frame

    def chat(self, messages, callback) -> None:
        self.chats.append(messages)
        self.callback = callback

    def save_storage(self) -> None:
        self.saved += 1

    def save_settings(self, values: dict) -> None:
        self.saved_settings = dict(values)
        self.settings.update(values)


def load_plugin():
    spec = importlib.util.spec_from_file_location("quiz_helper_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class QuizHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.module = load_plugin()
        self.timers: list[tuple[int, object]] = []
        self.module._schedule = lambda delay, callback: self.timers.append((delay, callback))
        self.module._ocr_factory = lambda: None
        FakeOcrTask.last = None

    def fire(self, delay: int) -> None:
        for index, (queued, callback) in enumerate(list(self.timers)):
            if queued == delay:
                self.timers.pop(index)
                callback()
                return
        raise AssertionError(f"没有排队的 {delay}ms 定时器")

    def trigger(self, api: FakeAPI, text: str = "这题怎么做") -> None:
        api.handlers["chat.user"][0](text=text)
        self.fire(300)

    def test_setup_registers_events(self) -> None:
        api = FakeAPI()
        self.module.setup(api)
        self.assertIn("chat.user", api.handlers)
        self.assertIn("pet.poke", api.handlers)
        self.assertIn("screen.context", api.handlers)
        self.assertTrue(api.logs)

    def test_trigger_captures_and_answers(self) -> None:
        api = FakeAPI({"show_window": False})
        self.module.setup(api)
        api.handlers["chat.user"][0](text="这题怎么做")
        self.assertEqual(api.captures, 0)
        self.fire(300)
        self.assertEqual(api.captures, 1)
        self.assertEqual(len(api.chats), 1)

        content = api.chats[0][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        api.callback("【题目】1 + 1 = ?\n【答案】2\n【解析】基础加法。", None)

        self.assertTrue(any("答案：2" in text for text in api.said))
        self.assertEqual(api.actions, ["nod"])
        self.assertEqual(api.storage["answered"], 1)

    def test_non_trigger_word_does_nothing(self) -> None:
        api = FakeAPI()
        self.module.setup(api)
        api.handlers["chat.user"][0](text="今天天气不错")
        self.assertEqual(api.captures, 0)
        self.assertEqual(len(self.timers), 0)

    def test_config_words_do_not_trigger(self) -> None:
        api = FakeAPI()
        self.module.setup(api)
        api.handlers["chat.user"][0](text="做题插件怎么用")
        self.assertEqual(api.captures, 0)

    def test_disabled_chat_trigger(self) -> None:
        api = FakeAPI({"enable_chat_trigger": False})
        self.module.setup(api)
        api.handlers["chat.user"][0](text="这题怎么做")
        self.assertEqual(api.captures, 0)

    def test_ocr_text_path(self) -> None:
        self.module._ocr_factory = lambda: FakeOcrTask
        api = FakeAPI({"show_window": False, "recognition": "auto"})
        self.module.setup(api)
        self.trigger(api)

        task = FakeOcrTask.last
        self.assertIsNotNone(task)
        self.assertEqual(api.chats, [])
        task.signals.done.emit("第 1 题：3 + 4 = ?\nA. 6  B. 7  C. 8", "")

        self.assertEqual(len(api.chats), 1)
        user = api.chats[0][1]["content"]
        self.assertIsInstance(user, str)
        self.assertIn("3 + 4", user)

        api.callback("【题目】3 + 4 = ?\n【答案】B. 7\n【解析】3 加 4 等于 7。", None)
        self.assertTrue(any("答案：B. 7" in text for text in api.said))

    def test_ocr_sensitive_lines_are_filtered(self) -> None:
        self.module._ocr_factory = lambda: FakeOcrTask
        api = FakeAPI({"show_window": False, "recognition": "text"})
        self.module.setup(api)
        self.trigger(api)
        FakeOcrTask.last.signals.done.emit(
            "登录密码：123456\n题目：5 × 6 = ?\n验证码 ABCD", ""
        )
        user = api.chats[0][1]["content"]
        self.assertIn("5 × 6", user)
        self.assertNotIn("123456", user)
        self.assertNotIn("ABCD", user)

    def test_ocr_empty_falls_back_to_image(self) -> None:
        self.module._ocr_factory = lambda: FakeOcrTask
        api = FakeAPI({"show_window": False, "recognition": "auto"})
        self.module.setup(api)
        self.trigger(api)
        FakeOcrTask.last.signals.done.emit("", "")

        self.assertEqual(len(api.chats), 1)
        content = api.chats[0][1]["content"]
        self.assertEqual(content[1]["type"], "image_url")

    def test_text_mode_without_ocr_text_fails(self) -> None:
        self.module._ocr_factory = lambda: FakeOcrTask
        api = FakeAPI({"show_window": False, "recognition": "text"})
        self.module.setup(api)
        self.trigger(api)
        FakeOcrTask.last.signals.done.emit("", "")

        self.assertEqual(api.chats, [])
        self.assertTrue(any("没读出来" in text for text in api.said))

    def test_no_question_reply(self) -> None:
        api = FakeAPI({"show_window": False})
        self.module.setup(api)
        self.trigger(api)
        api.callback("【无题目】", None)

        self.assertTrue(any("没看到明确" in text for text in api.said))
        self.assertNotIn("answered", api.storage)

    def test_vision_rejection_suggests_ocr(self) -> None:
        api = FakeAPI({"show_window": False})
        self.module.setup(api)
        self.trigger(api)
        api.callback(None, "接口返回 400。content must be a string")

        self.assertTrue(any("看不了图片" in text for text in api.said))
        self.assertEqual(api.storage, {})

    def test_ocr_timeout_falls_back_to_image(self) -> None:
        self.module._ocr_factory = lambda: FakeOcrTask
        api = FakeAPI({"show_window": False, "recognition": "auto"})
        self.module.setup(api)
        self.trigger(api)
        self.fire(23000)

        self.assertEqual(len(api.chats), 1)
        self.assertEqual(api.chats[0][1]["content"][1]["type"], "image_url")

    def test_poke_trigger_setting(self) -> None:
        api = FakeAPI({"poke_trigger": True})
        self.module.setup(api)
        api.handlers["pet.poke"][0]()
        self.fire(300)
        self.assertEqual(api.captures, 1)

    def test_screen_context_note(self) -> None:
        api = FakeAPI()
        self.module.setup(api)
        context = SimpleNamespace(title="在线考试 - Chrome", process="chrome", notes=[])
        api.handlers["screen.context"][0](context)
        self.assertEqual(len(context.notes), 1)
        self.assertIn("做题", context.notes[0])

    def test_prompt_provider_marks_quiz_request(self) -> None:
        api = FakeAPI()
        self.module.setup(api)
        provider = api.prompt_provider
        self.assertIsNotNone(provider)
        self.assertIn("做题助手", provider(query="这题怎么做"))
        self.assertEqual(provider(query="今天天气不错"), "")

    def test_options_fallbacks(self) -> None:
        api = FakeAPI(
            {
                "trigger_words": "  ",
                "recognition": "video",
                "detail": "huge",
                "show_window": "false",
            }
        )
        opts = self.module._options(api)
        self.assertEqual(opts["triggers"], self.module.DEFAULT_TRIGGERS)
        self.assertEqual(opts["recognition"], "auto")
        self.assertEqual(opts["detail"], "standard")
        self.assertFalse(opts["show_window"])

    def test_section_parses_multiline_answer(self) -> None:
        text = "【题目】选择题\n【答案】C. 8\n【解析】第一行\n第二行"
        self.assertEqual(self.module._section(text, "答案"), "C. 8")
        self.assertEqual(self.module._section(text, "解析"), "第一行 第二行")
        self.assertEqual(self.module._section(text, "不存在的字段"), "")

    def test_result_window_shows_full_reply(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])  # noqa: F841
        api = FakeAPI({"show_window": True})
        self.module.setup(api)
        self.trigger(api)
        api.callback("【题目】2 + 2 = ?\n【答案】4\n【解析】两个 2 相加。", None)

        window = self.module._state["window"]
        self.assertIsNotNone(window)
        self.assertIn("两个 2 相加", window.editor.toPlainText())
        self.assertTrue(any("窗口里" in text for text in api.said))
        window.close()
        window.deleteLater()

    def test_settings_dialog_saves(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])  # noqa: F841
        api = FakeAPI({"show_window": True, "recognition": "auto"})
        self.module.setup(api)
        dialog = self.module.QuizSettingsDialog(api)
        try:
            dialog.words_edit.setText("解题，讲题")
            dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("text"))
            dialog.detail_combo.setCurrentIndex(dialog.detail_combo.findData("detailed"))
            dialog.window_check.setChecked(False)
            dialog.poke_check.setChecked(True)
            dialog.extra_edit.setText("只给答案")
            dialog._save()

            saved = api.saved_settings
            self.assertEqual(saved["trigger_words"], "解题，讲题")
            self.assertEqual(saved["recognition"], "text")
            self.assertEqual(saved["detail"], "detailed")
            self.assertFalse(saved["show_window"])
            self.assertTrue(saved["poke_trigger"])
            self.assertEqual(saved["extra"], "只给答案")
            self.assertTrue(any("设置已保存" in text for text in api.said))
        finally:
            dialog.close()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
