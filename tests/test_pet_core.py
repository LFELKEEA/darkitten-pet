from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from config import Config
from conversation import Conversation
from plugin_manager import PluginManager
from screen_watcher import (
    SCREEN_SYSTEM_PROMPT,
    ScreenContext,
    ScreenWatcher,
    WindowInfo,
    capture_screen,
)
from ai_client import AIError, request_chat, supports_vision

APP = QApplication.instance() or QApplication([])


class FakePet:
    ACTION_SPECS = {"hop": (0.8, True)}

    def __init__(self) -> None:
        self.said: list[str] = []
        self.actions: list[str] = []
        self._hidden_by_user = False
        self._sleeping = False
        self._chat_open = False
        self._voice_active = False
        self._last_interaction = 0.0

    def say(self, text: str, duration: int = 5000) -> None:
        self.said.append(text)

    def start_action(self, name: str, announce: bool = True) -> None:
        self.actions.append(name)


def write_plugin(
    root: Path,
    plugin_id: str,
    body: str,
    permissions: list[str] | None = None,
    settings: dict | None = None,
) -> Path:
    folder = root / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    perms = permissions if permissions is not None else ["pet.say", "events", "timer", "settings"]
    import json

    manifest = {
        "id": plugin_id,
        "name": plugin_id,
        "version": "1.0.0",
        "api_version": 1,
        "entry": "main.py",
        "description": "test",
        "author": "test",
        "permissions": perms,
        "settings": settings or {},
    }
    (folder / "plugin.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (folder / "main.py").write_text(body, encoding="utf-8")
    return folder


class ConversationTests(unittest.TestCase):
    def test_append_limit_and_clear(self) -> None:
        conversation = Conversation(limit=4)
        for index in range(6):
            conversation.append("user", f"m{index}")
        self.assertEqual(len(conversation), 4)
        self.assertEqual(conversation.messages()[0]["content"], "m2")
        self.assertEqual(conversation.last_user(), "m5")
        conversation.clear()
        self.assertEqual(len(conversation), 0)


class PluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugins_root = self.root / "plugins"
        self.plugins_root.mkdir()
        self.config = Config(path=self.root / "config.json")
        self.pet = FakePet()
        self.conversation = Conversation()
        self.manager = PluginManager(
            self.config,
            self.pet,
            self.conversation,
            data_dir=self.root,
            plugins_dir=self.plugins_root,
        )

    def tearDown(self) -> None:
        self.manager.shutdown()
        self.temp.cleanup()

    def test_enable_and_run(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.hello",
            "def setup(api):\n    api.say('hello')\n",
        )
        self.manager.discover()
        self.assertTrue(self.manager.set_enabled("demo.hello", True))
        self.assertEqual(self.pet.said, ["hello"])
        self.assertTrue(self.config.get("plugins", {})["enabled"]["demo.hello"])
        info = self.manager.get("demo.hello")
        self.assertIsNotNone(info)
        self.assertEqual(info.status, "运行中")

        self.manager.set_enabled("demo.hello", False)
        self.assertEqual(self.manager.get("demo.hello").status, "未启用")

    def test_permission_denied(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.noperm",
            "def setup(api):\n    api.say('should fail')\n",
            permissions=[],
        )
        self.manager.discover()
        self.assertFalse(self.manager.load_plugin("demo.noperm"))
        info = self.manager.get("demo.noperm")
        self.assertEqual(info.status, "加载失败")
        self.assertIn("pet.say", info.error)

    def test_plugin_settings_window_hook(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.ui",
            "WINDOW = None\n"
            "def setup(api):\n"
            "    pass\n"
            "def open_settings(api, parent=None):\n"
            "    global WINDOW\n"
            "    WINDOW = FakeWindow()\n"
            "    return WINDOW\n",
        )
        folder = self.plugins_root / "demo.ui"
        helper = (
            "class FakeWindow:\n"
            "    def __init__(self):\n"
            "        self.shown = 0\n"
            "        self.closed = 0\n"
            "        self.destroyed = self\n"
            "    def connect(self, callback):\n"
            "        self.callback = callback\n"
            "    def show(self):\n"
            "        self.shown += 1\n"
            "    def raise_(self):\n"
            "        pass\n"
            "    def activateWindow(self):\n"
            "        pass\n"
            "    def deleteLater(self):\n"
            "        pass\n"
            "    def close(self):\n"
            "        self.closed += 1\n"
        )
        main_path = folder / "main.py"
        main_path.write_text(helper + main_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.manager.discover()
        self.assertTrue(self.manager.set_enabled("demo.ui", True))
        self.assertTrue(self.manager.has_ui("demo.ui"))
        self.assertTrue(self.manager.open_ui("demo.ui"))
        window = self.manager._ui_windows["demo.ui"]
        self.assertEqual(window.shown, 1)
        self.assertTrue(self.manager.open_ui("demo.ui"))
        self.assertEqual(window.shown, 2)
        self.manager.set_enabled("demo.ui", False)
        self.assertEqual(window.closed, 1)
        self.assertNotIn("demo.ui", self.manager._ui_windows)

    def test_plugin_without_settings_window(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.noui",
            "def setup(api):\n    pass\n",
        )
        self.manager.discover()
        self.assertTrue(self.manager.load_plugin("demo.noui"))
        self.assertFalse(self.manager.has_ui("demo.noui"))
        self.assertFalse(self.manager.open_ui("demo.noui"))

    def test_callback_error_is_isolated(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.boom",
            "def setup(api):\n"
            "    def boom():\n"
            "        raise RuntimeError('kaboom')\n"
            "    api.on('startup', boom)\n",
        )
        self.manager.discover()
        self.assertTrue(self.manager.load_plugin("demo.boom"))
        info = self.manager.get("demo.boom")
        self.assertEqual(info.status, "运行出错（已隔离）")
        self.assertIn("kaboom", info.error)
        log = (self.root / "plugins.log").read_text(encoding="utf-8")
        self.assertIn("kaboom", log)

    def test_storage_and_settings(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.store",
            "def setup(api):\n"
            "    api.storage['count'] = api.storage.get('count', 0) + 1\n"
            "    api.save_storage()\n"
            "    api.save_settings({'ok': True})\n",
            permissions=["storage", "settings"],
        )
        self.manager.discover()
        self.assertTrue(self.manager.load_plugin("demo.store"))
        self.assertEqual(self.manager.load_storage("demo.store")["count"], 1)
        self.assertTrue(self.manager.plugin_settings("demo.store")["ok"])

    def test_manifest_settings_are_defaults(self) -> None:
        write_plugin(
            self.plugins_root,
            "demo.defaults",
            "def setup(api):\n"
            "    api.save_settings({'saved': api.settings['interval']})\n",
            permissions=["settings"],
            settings={"interval": 10, "mode": "at"},
        )
        self.manager.discover()
        self.assertEqual(self.manager.plugin_settings("demo.defaults")["interval"], 10)
        self.assertTrue(self.manager.load_plugin("demo.defaults"))
        values = self.manager.plugin_settings("demo.defaults")
        self.assertEqual(values["interval"], 10)
        self.assertEqual(values["mode"], "at")
        self.assertEqual(values["saved"], 10)


class AIClientTests(unittest.TestCase):
    def test_vision_content_is_passed_through(self) -> None:
        captured = {}

        def fake_http(url, payload, headers, timeout):
            captured["url"] = url
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "看到了"}}]}

        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "看看屏幕"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
            ]}
        ]
        with mock.patch("ai_client._http_json", side_effect=fake_http):
            reply = request_chat(
                {"provider": "openai", "model": "gpt-4o", "base_url": "https://example.com/v1", "api_key": "test", "timeout": 10},
                messages,
            )
        self.assertEqual(reply, "看到了")
        content = captured["payload"]["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("reasoning_effort", captured["payload"])

    def test_deepseek_disables_thinking(self) -> None:
        captured = {}

        def fake_http(url, payload, headers, timeout):
            captured["payload"] = payload
            return {"choices": [{"finish_reason": "stop", "message": {"content": "哼"}}]}

        with mock.patch("ai_client._http_json", side_effect=fake_http):
            reply = request_chat(
                {
                    "provider": "openai",
                    "model": "deepseek-v4.1-flash-1017",
                    "base_url": "https://example.com/v1",
                    "api_key": "test",
                    "timeout": 10,
                },
                [{"role": "user", "content": "在吗"}],
            )
        self.assertEqual(reply, "哼")
        self.assertEqual(captured["payload"]["reasoning_effort"], "none")

    def test_rejected_reasoning_param_falls_back(self) -> None:
        payloads = []

        def fake_http(url, payload, headers, timeout):
            payloads.append(dict(payload))
            if "reasoning_effort" in payload:
                raise AIError("接口返回 400。unknown field reasoning_effort")
            return {"choices": [{"finish_reason": "stop", "message": {"content": "好"}}]}

        with mock.patch("ai_client._http_json", side_effect=fake_http):
            reply = request_chat(
                {
                    "provider": "openai",
                    "model": "deepseek-chat",
                    "base_url": "https://example.com/v1",
                    "api_key": "test",
                    "timeout": 10,
                },
                [{"role": "user", "content": "在吗"}],
            )
        self.assertEqual(reply, "好")
        self.assertEqual(len(payloads), 2)
        self.assertNotIn("reasoning_effort", payloads[1])

    def test_empty_reply_retries_with_larger_budget(self) -> None:
        payloads = []

        def fake_http(url, payload, headers, timeout):
            payloads.append(dict(payload))
            if len(payloads) == 1:
                return {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "", "reasoning_content": "思考"},
                        }
                    ],
                    "usage": {"completion_tokens": 500},
                }
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": '{"memories": []}'}}]
            }

        with mock.patch("ai_client._http_json", side_effect=fake_http):
            reply = request_chat(
                {
                    "provider": "openai",
                    "model": "deepseek-chat",
                    "base_url": "https://example.com/v1",
                    "api_key": "test",
                    "timeout": 10,
                },
                [{"role": "user", "content": "整理"}],
            )
        self.assertEqual(reply, '{"memories": []}')
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[1]["max_tokens"], 1500)

    def test_empty_reply_reports_diagnostics(self) -> None:
        calls = []

        def fake_http(url, payload, headers, timeout):
            calls.append(dict(payload))
            return {
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": "", "reasoning_content": "思考"},
                    }
                ],
                "usage": {"completion_tokens": 500},
            }

        with mock.patch("ai_client._http_json", side_effect=fake_http):
            with self.assertRaises(AIError) as ctx:
                request_chat(
                    {
                        "provider": "openai",
                        "model": "deepseek-chat",
                        "base_url": "https://example.com/v1",
                        "api_key": "test",
                        "timeout": 10,
                    },
                    [{"role": "user", "content": "整理"}],
                )
        message = str(ctx.exception)
        self.assertIn("空回复", message)
        self.assertIn("finish_reason=length", message)
        self.assertIn("completion_tokens=500", message)
        self.assertIn("reasoning_content=2 字", message)
        self.assertEqual(len(calls), 2)


class ScreenTests(unittest.TestCase):
    def test_vision_capability(self) -> None:
        self.assertTrue(supports_vision({"provider": "openai", "model": "gpt-4o-mini"}))
        self.assertFalse(supports_vision({"provider": "pollinations", "model": "openai-fast"}))
        self.assertFalse(supports_vision({"provider": "openai", "model": "deepseek-chat"}))

    def test_window_sensitive_flags(self) -> None:
        self.assertTrue(WindowInfo(title="请输入密码", process="chrome").sensitive)
        self.assertTrue(WindowInfo(title="聊天", process="keepass").sensitive)
        self.assertFalse(WindowInfo(title="Visual Studio Code", process="code").sensitive)

    def test_prompt_contains_ocr_and_notes(self) -> None:
        watcher = ScreenWatcher(Config(path=Path(tempfile.gettempdir()) / "nope.json"), FakePet(), Conversation())
        context = ScreenContext(title="编辑器", process="code", ocr_text="def hello():", notes=["在写代码"])
        prompt = watcher._user_prompt(context, "ocr")
        self.assertIn("def hello():", prompt)
        self.assertIn("在写代码", prompt)
        self.assertIn("SKIP", SCREEN_SYSTEM_PROMPT)

    def test_auto_mode(self) -> None:
        config = Config(path=Path(tempfile.gettempdir()) / "screen_mode.json")
        config.ai["provider"] = "pollinations"
        watcher = ScreenWatcher(config, FakePet(), Conversation())
        self.assertEqual(watcher._resolved_mode(), "window")
        config.ai["provider"] = "openai"
        config.ai["model"] = "gpt-4o-mini"
        self.assertEqual(watcher._resolved_mode(), "vision")
        watcher._vision_blocked = True
        self.assertEqual(watcher._resolved_mode(), "window")

    def test_capture_does_not_crash(self) -> None:
        frame = capture_screen(max_edge=640, quality=60)
        self.assertIsInstance(frame, ScreenContext)
        if frame.image_jpeg:
            self.assertGreater(frame.width, 0)
            self.assertGreater(frame.height, 0)

    def test_sensitive_window_is_skipped(self) -> None:
        config = Config(path=Path(tempfile.gettempdir()) / "screen_skip.json")
        config.set("screen_watch", {"enabled": True, "pause_sensitive": True, "mode": "window"}, save=False)
        pet = FakePet()
        watcher = ScreenWatcher(config, pet, Conversation())
        with mock.patch("screen_watcher._foreground_window", return_value=WindowInfo(title="密码", process="chrome")):
            watcher.observe(manual=True)
        self.assertTrue(any("隐私" in item for item in pet.said))
        watcher.shutdown()

    def test_plugin_can_skip_before_capture(self) -> None:
        class SkipPlugins:
            def emit(self, event, **payload):
                if event == "screen.context":
                    payload["context"].skip = True
                    payload["context"].skip_reason = "插件跳过"

        config = Config(path=Path(tempfile.gettempdir()) / "screen_plugin_skip.json")
        config.ai["provider"] = "openai"
        config.ai["model"] = "gpt-4o-mini"
        config.set("screen_watch", {"enabled": True, "mode": "vision"}, save=False)
        pet = FakePet()
        watcher = ScreenWatcher(config, pet, Conversation(), SkipPlugins())
        with mock.patch("screen_watcher._foreground_window", return_value=WindowInfo(title="编辑器", process="code")):
            with mock.patch.object(watcher, "_capture", side_effect=AssertionError("should not capture")):
                watcher.observe(manual=False)
        self.assertEqual(watcher.last_status, "插件跳过")
        watcher.shutdown()


if __name__ == "__main__":
    unittest.main()
