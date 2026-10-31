from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import text, voicepacks  # noqa: E402
from lfelkeea_qq_bridge.service import (  # noqa: E402
    QQBridgeService,
    normalize_settings,
)


class FakeAPI:
    version = 1

    def __init__(self, settings: dict | None = None, state_dir: Path | None = None) -> None:
        self.settings = dict(settings or {})
        self.said: list[str] = []
        self.logs: list[str] = []
        self.saved: list[dict] = []
        self.timers: list[tuple[int, object]] = []
        self.chat_calls: list[list[dict]] = []
        self.reply = "好的。"
        self.chat_error: str | None = None
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=state_dir or Path(tempfile.mkdtemp()))

    def say(self, message: str, duration: int = 5000) -> None:
        self.said.append(message)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def save_settings(self, values: dict) -> None:
        self.saved.append(dict(values))
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        self.timers.append((interval_ms, callback))

    def chat(self, messages, callback) -> None:
        self.chat_calls.append(messages)
        callback(self.reply, self.chat_error)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[dict]]] = []
        self.event = threading.Event()

    def send(self, conversation: str, segments: list[dict]) -> None:
        self.sent.append((conversation, segments))
        self.event.set()


def make_audio(folder: Path, name: str = "clip.wav") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


class VoiceUtilityTests(unittest.TestCase):
    def test_normalize_defaults(self) -> None:
        settings = normalize_settings({})
        self.assertEqual(settings["voice_packs"], [])
        self.assertFalse(settings["voice_reply_enabled"])
        self.assertAlmostEqual(settings["voice_reply_probability"], 0.08)
        settings = normalize_settings(
            {
                "voice_packs": [" C:/a ", "C:/a", "", "D:/b"],
                "voice_reply_enabled": "on",
                "voice_reply_probability": "9",
            }
        )
        self.assertEqual(settings["voice_packs"], ["C:/a", "D:/b"])
        self.assertTrue(settings["voice_reply_enabled"])
        self.assertEqual(settings["voice_reply_probability"], 1.0)

    def test_parse_voice_tag(self) -> None:
        cleaned, wanted = text.parse_voice_tag("好呀[voice]")
        self.assertTrue(wanted)
        self.assertEqual(cleaned, "好呀")
        cleaned, wanted = text.parse_voice_tag("好呀【语音】")
        self.assertTrue(wanted)
        self.assertEqual(cleaned, "好呀")
        cleaned, wanted = text.parse_voice_tag("好呀[语音]")
        self.assertTrue(wanted)
        self.assertEqual(cleaned, "好呀")

    def test_build_segments_record(self) -> None:
        segments = text.build_segments("你好", records=["C:/a.wav"])
        self.assertEqual(segments[-1], {"type": "record", "data": {"file": "C:/a.wav"}})

    def test_find_clips_and_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack = root / "我的语音包"
            make_audio(pack)
            make_audio(pack, "second.mp3")
            (pack / "readme.txt").write_text("x", encoding="utf-8")
            clips = voicepacks.find_clips(pack)
            self.assertEqual(len(clips), 2)
            packs = voicepacks.scan_packs([root], max_depth=2)
            self.assertTrue(any(item.path == pack.resolve() for item in packs))
            self.assertIn(voicepacks.choose_clip(pack).suffix, {".mp3", ".wav"})

    def test_scan_ignores_empty_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "语音包").mkdir()
            (root / "语音包" / "a.txt").write_text("x", encoding="utf-8")
            self.assertEqual(voicepacks.scan_packs([root], max_depth=2), [])


class VoiceServiceTests(unittest.TestCase):
    def make_service(self, settings: dict | None = None):
        state = Path(tempfile.mkdtemp())
        api = FakeAPI(
            {
                "owner_qq": "10001",
                "allow_private": ["10001"],
                "allow_groups": ["123456"],
                "auto_face": False,
                "face_probability": 0,
                "sticker_probability": 0,
                **dict(settings or {}),
            },
            state_dir=state,
        )
        service = QQBridgeService(api)
        service.client = FakeClient()
        service.started = True
        return api, service

    def test_send_voice_record(self) -> None:
        api, service = self.make_service()
        with tempfile.TemporaryDirectory() as tmp:
            clip = make_audio(Path(tmp))
            result: list[tuple[bool, str]] = []
            service.send_voice("private:10001", str(clip), lambda ok, msg: result.append((ok, msg)))
            self.assertTrue(service.client.event.wait(2))
        conversation, segments = service.client.sent[-1]
        self.assertEqual(conversation, "private:10001")
        self.assertEqual(segments, [{"type": "record", "data": {"file": str(clip.resolve())}}])

    def test_send_voice_rejects_stranger(self) -> None:
        api, service = self.make_service()
        with tempfile.TemporaryDirectory() as tmp:
            clip = make_audio(Path(tmp))
            result: list[tuple[bool, str]] = []
            service.send_voice("private:22222", str(clip), lambda ok, msg: result.append((ok, msg)))
        self.assertEqual(result and result[0][0], False)
        self.assertIn("白名单", result[0][1])
        self.assertEqual(service.client.sent, [])

    def test_pick_voice_clip_explicit_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = make_audio(Path(tmp) / "pack")
            api, service = self.make_service({"voice_packs": [str(clip.parent)]})
            self.assertEqual(service._pick_voice_clip(False), "")
            self.assertTrue(service._pick_voice_clip(True))

    def test_reply_with_voice_tag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = make_audio(Path(tmp) / "pack")
            api, service = self.make_service(
                {"voice_packs": [str(clip.parent)], "voice_reply_enabled": False}
            )
            api.reply = "给你听个声音[voice]"
            service._on_event_main(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 10001,
                    "self_id": 99999,
                    "message_id": 9,
                    "message": [{"type": "text", "data": {"text": "发个语音"}}],
                }
            )
            deadline = time.time() + 2
            while time.time() < deadline and not any(
                segment.get("type") == "record"
                for _, segments in service.client.sent
                for segment in segments
            ):
                time.sleep(0.02)
        records = [
            segment
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "record"
        ]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["data"]["file"].endswith(".wav"))
        texts = [
            segment["data"]["text"]
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "text"
        ]
        self.assertIn("给你听个声音", texts)
        self.assertNotIn("[voice]", "".join(texts))

    def test_voice_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clip = make_audio(Path(tmp) / "pack")
            api, service = self.make_service({"voice_packs": [str(clip.parent)]})
            service._on_event_main(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 10001,
                    "self_id": 99999,
                    "message_id": 11,
                    "message": [{"type": "text", "data": {"text": "/voice"}}],
                }
            )
            self.assertTrue(service.client.event.wait(2))
        records = [
            segment
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "record"
        ]
        self.assertEqual(len(records), 1)


if __name__ == "__main__":
    unittest.main()
