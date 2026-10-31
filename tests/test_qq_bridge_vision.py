from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import service as service_module, text, vision  # noqa: E402
from lfelkeea_qq_bridge.service import QQBridgeService  # noqa: E402

# 1x1 透明 PNG
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000000020001e221bc330000000049454e44ae426082"
)


class ImageMessageTests(unittest.TestCase):
    def test_image_sources_from_segments(self) -> None:
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "self_id": 99999,
            "message_id": 7,
            "message": [
                {"type": "image", "data": {"file": "a.jpg", "url": "https://example.com/a.jpg"}},
                {"type": "text", "data": {"text": "看看这个"}},
            ],
        }
        message = text.parse_event(payload)
        self.assertIsNotNone(message)
        assert message is not None
        self.assertTrue(message.has_image)
        self.assertEqual(text.image_sources(message), ["https://example.com/a.jpg"])

    def test_image_sources_skip_unreachable(self) -> None:
        payload = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 10001,
            "self_id": 99999,
            "message_id": 8,
            "message": [{"type": "image", "data": {"file": "relative.jpg"}}],
        }
        message = text.parse_event(payload)
        assert message is not None
        self.assertEqual(text.image_sources(message), [])


class VisionUtilityTests(unittest.TestCase):
    def test_prepare_local_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.png"
            path.write_bytes(PNG)
            prepared = vision.prepare([str(path)])
        self.assertEqual(len(prepared.uris), 1)
        self.assertTrue(prepared.uris[0].startswith("data:image/png;base64,"))
        self.assertEqual(prepared.blobs[0][1], "image/png")

    def test_missing_file_returns_error(self) -> None:
        prepared = vision.prepare(["C:/definitely/not/here.png"])
        self.assertEqual(prepared.uris, [])
        self.assertEqual(prepared.error, "图片下载失败")

    def test_vision_rejection_detection(self) -> None:
        self.assertTrue(vision.looks_like_vision_rejection("invalid content"))
        self.assertTrue(vision.looks_like_vision_rejection("model does not support image"))
        self.assertFalse(vision.looks_like_vision_rejection("连接超时"))


class FakeAPI:
    version = 1

    def __init__(self) -> None:
        self.settings: dict = {}
        self.chat_calls: list[list[dict]] = []
        self.chat_error: str | None = None
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=Path(tempfile.mkdtemp()))

    def say(self, message: str, duration: int = 5000) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def save_settings(self, values: dict) -> None:
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        pass

    def chat(self, messages, callback) -> None:
        self.chat_calls.append(messages)
        callback("", self.chat_error)


class ChatContentTests(unittest.TestCase):
    def setUp(self) -> None:
        with mock.patch.object(
            service_module, "SnowLumaManager"
        ):
            self.service = QQBridgeService(FakeAPI())
        self.service._pending["private:1"] = time.time()

    def test_image_content_uses_image_url_parts(self) -> None:
        self.service.api.chat = mock.Mock(
            side_effect=lambda messages, cb: cb("", None)
        )
        self.service._start_chat(
            "private:1", "sys", "看看图", ["data:image/png;base64,AAA"], "", lambda *_: None
        )
        messages = self.service.api.chat.call_args[0][0]
        content = messages[1]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(content[0], {"type": "text", "text": "看看图"})
        self.assertEqual(content[1]["type"], "image_url")

    def test_vision_rejection_falls_back_to_text(self) -> None:
        api = self.service.api
        api.chat_error = "invalid content"
        api.chat = mock.Mock(side_effect=lambda messages, cb: cb("", api.chat_error))
        prepared = vision.PreparedImages(blobs=[(PNG, "image/png")])
        with mock.patch.object(
            self.service, "_retry_without_images"
        ) as retry:
            self.service._start_chat(
                "private:1",
                "sys",
                "看看图",
                ["data:image/png;base64,AAA"],
                "",
                lambda *_: None,
                prepared,
            )
        self.assertTrue(self.service._vision_blocked)
        retry.assert_called_once()

    def test_ocr_text_is_added_to_prompt(self) -> None:
        api = self.service.api
        api.chat = mock.Mock(side_effect=lambda messages, cb: cb("", None))
        self.service._start_chat(
            "private:1", "sys", "看看图", [], "图片里的字", lambda *_: None
        )
        content = api.chat.call_args[0][0][1]["content"]
        self.assertIn("图片里的字", content)


if __name__ == "__main__":
    unittest.main()
