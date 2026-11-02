from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai_client import AIRequest  # noqa: E402
from chat import ChatWindow  # noqa: E402
from config import Config  # noqa: E402
from conversation import Conversation  # noqa: E402
from plugin_manager import PluginManager  # noqa: E402

APP = QApplication.instance() or QApplication([])
PLUGIN_ID = "lfelkeea.alive_core"


class FakePet:
    def say(self, *args, **kwargs) -> None:
        pass

    def action(self, *args, **kwargs) -> None:
        pass


class AliveHostIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.config = Config(path=self.root / "config.json")
        self.conversation = Conversation(24)
        self.manager = PluginManager(
            self.config,
            FakePet(),
            self.conversation,
            data_dir=self.root,
            plugins_dir=ROOT / "plugins",
        )
        self.manager.discover()
        self.assertTrue(self.manager.load_plugin(PLUGIN_ID))
        self.chat = ChatWindow(
            self.config,
            self.conversation,
            context_provider=self.manager.prompt_context,
        )
        self.service = self.manager._loaded[PLUGIN_ID].module._service

    def tearDown(self) -> None:
        self.chat.deleteLater()
        self.manager.shutdown()

    def test_provider_is_registered_and_unloaded(self) -> None:
        self.assertEqual(len(self.manager._prompt_providers.get(PLUGIN_ID, [])), 1)
        self.manager.unload_plugin(PLUGIN_ID)
        self.assertEqual(self.manager._prompt_providers.get(PLUGIN_ID, []), [])

    def test_chat_prompt_injects_once_even_with_shim(self) -> None:
        system = self.chat._system_prompt("我今天有点累")
        self.assertIn("【活人感上下文】", system)
        request = AIRequest(
            dict(self.config.ai),
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "我今天有点累"},
            ],
        )
        self.assertEqual(request.messages[0]["content"].count("【活人感上下文】"), 1)

    def test_other_plugin_prompt_is_untouched(self) -> None:
        request = AIRequest(
            dict(self.config.ai),
            [
                {"role": "system", "content": "QQ 桥接自己的提示词"},
                {"role": "user", "content": "在吗"},
            ],
        )
        self.assertNotIn("【活人感上下文】", request.messages[0]["content"])

    def test_chat_turns_feed_memory(self) -> None:
        self.chat.user_sent.connect(
            lambda text: self.manager.emit("chat.user", text=text)
        )
        self.chat.user_sent.emit("我最近在学做菜")
        self.service._on_conversation_message("assistant", "那你最拿手的是什么？")
        texts = [row.get("text", "") for row in self.service.memory_rows()]
        self.assertTrue(any("学做菜" in text for text in texts))

    def test_screen_prompt_does_not_expose_private_turns(self) -> None:
        self.service.memory.add_item(
            "主人的旧密码是1234", kind="fact", visibility="private"
        )
        self.service.working.record(
            "user", "我今晚要去见一个很重要的人", visibility="private"
        )
        block = self.manager.prompt_context(
            query="屏幕", viewer="owner", source="screen"
        )
        self.assertNotIn("1234", block)
        self.assertNotIn("很重要的人", block)


if __name__ == "__main__":
    unittest.main()
