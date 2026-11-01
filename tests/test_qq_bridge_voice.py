from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import text, tts  # noqa: E402
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


def make_audio(folder: Path, name: str = "sample.wav") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


class TTSUtilityTests(unittest.TestCase):
    def test_normalize_defaults(self) -> None:
        settings = normalize_settings({})
        self.assertEqual(settings["tts_engine"], "windows")
        self.assertEqual(settings["tts_windows_voice"], "")
        self.assertEqual(settings["tts_gpt_sovits_url"], "http://127.0.0.1:9880")
        self.assertEqual(settings["tts_ref_audio"], "")
        self.assertEqual(settings["tts_prompt_lang"], "zh")
        self.assertEqual(settings["tts_text_lang"], "zh")
        self.assertAlmostEqual(settings["tts_speed"], 1.0)
        self.assertFalse(settings["tts_reply_enabled"])
        self.assertAlmostEqual(settings["tts_reply_probability"], 0.08)
        self.assertEqual(settings["tts_max_chars"], 120)

    def test_normalize_clamps_and_sanitizes(self) -> None:
        settings = normalize_settings(
            {
                "tts_engine": "GPT_SOVITS",
                "tts_gpt_sovits_url": " http://127.0.0.1:9880 ",
                "tts_ref_audio": '"C:/voice/ref.wav"',
                "tts_rate": 99,
                "tts_volume": -5,
                "tts_speed": 9,
                "tts_reply_enabled": "on",
                "tts_reply_probability": "2",
                "tts_max_chars": 5000,
                "tts_prompt_lang": "jp",
                "tts_text_lang": "ja",
            }
        )
        self.assertEqual(settings["tts_engine"], "gpt_sovits")
        self.assertEqual(settings["tts_gpt_sovits_url"], "http://127.0.0.1:9880")
        self.assertEqual(settings["tts_ref_audio"], "C:/voice/ref.wav")
        self.assertEqual(settings["tts_rate"], 10)
        self.assertEqual(settings["tts_volume"], 0)
        self.assertEqual(settings["tts_speed"], 2.0)
        self.assertTrue(settings["tts_reply_enabled"])
        self.assertEqual(settings["tts_reply_probability"], 1.0)
        self.assertEqual(settings["tts_max_chars"], 500)
        self.assertEqual(settings["tts_prompt_lang"], "zh")
        self.assertEqual(settings["tts_text_lang"], "ja")

    def test_invalid_engine_falls_back(self) -> None:
        self.assertEqual(normalize_settings({"tts_engine": "edge"})["tts_engine"], "windows")

    def test_parse_voice_tag(self) -> None:
        for value in ("好呀[voice]", "好呀【语音】", "好呀[语音]"):
            cleaned, wanted = text.parse_voice_tag(value)
            self.assertTrue(wanted)
            self.assertEqual(cleaned, "好呀")

    def test_build_segments_record(self) -> None:
        segments = text.build_segments("你好", records=["C:/a.wav"])
        self.assertEqual(segments[-1], {"type": "record", "data": {"file": "C:/a.wav"}})

    def test_clean_text(self) -> None:
        self.assertEqual(tts.clean_text("[face:1] 你好 https://x.com 啊"), "你好 啊")
        self.assertEqual(tts.clean_text("abcdef", 3), "abc")
        self.assertEqual(tts.clean_text("   "), "")

    def test_gpt_sovits_endpoint(self) -> None:
        self.assertEqual(
            tts.gpt_sovits_endpoint("127.0.0.1:9880"), "http://127.0.0.1:9880/tts"
        )
        self.assertEqual(
            tts.gpt_sovits_endpoint("http://127.0.0.1:9880/tts"),
            "http://127.0.0.1:9880/tts",
        )
        with self.assertRaises(tts.TTSError):
            tts.gpt_sovits_endpoint("   ")


class TTSServiceTests(unittest.TestCase):
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

    def test_tts_ready(self) -> None:
        api, service = self.make_service()
        self.assertTrue(service._tts_ready())
        api, service = self.make_service({"tts_engine": "gpt_sovits"})
        self.assertFalse(service._tts_ready())
        api, service = self.make_service(
            {"tts_engine": "gpt_sovits", "tts_ref_audio": "C:/voice/ref.wav"}
        )
        self.assertTrue(service._tts_ready())

    def test_should_tts(self) -> None:
        api, service = self.make_service()
        self.assertTrue(service._should_tts(True))
        self.assertFalse(service._should_tts(False))
        api, service = self.make_service(
            {"tts_reply_enabled": True, "tts_reply_probability": 0.0}
        )
        self.assertFalse(service._should_tts(False))
        api, service = self.make_service(
            {"tts_reply_enabled": True, "tts_reply_probability": 1.0}
        )
        self.assertTrue(service._should_tts(False))
        api, service = self.make_service(
            {
                "tts_engine": "gpt_sovits",
                "tts_reply_enabled": True,
                "tts_reply_probability": 1.0,
            }
        )
        self.assertFalse(service._should_tts(True))

    def test_synthesize_uses_windows_engine(self) -> None:
        api, service = self.make_service({"tts_max_chars": 20})
        calls: list[tuple[str, dict]] = []

        def fake_windows(value, out, **kwargs):
            calls.append((value, kwargs))
            Path(out).write_bytes(b"RIFF" + b"0" * 2000)
            return Path(out)

        with mock.patch.object(tts, "synthesize_windows", fake_windows):
            path = service._synthesize_tts("[face:1] 你好呀")
        self.assertTrue(path.is_file())
        self.assertEqual(calls[0][0], "你好呀")
        self.assertEqual(calls[0][1]["voice"], "")
        self.assertEqual(calls[0][1]["rate"], 0)
        self.assertEqual(calls[0][1]["volume"], 100)

    def test_synthesize_uses_gpt_sovits(self) -> None:
        api, service = self.make_service(
            {
                "tts_engine": "gpt_sovits",
                "tts_gpt_sovits_url": "http://127.0.0.1:9880",
                "tts_ref_audio": "C:/voice/ref.wav",
                "tts_prompt_text": "参考文字",
                "tts_prompt_lang": "zh",
                "tts_text_lang": "ja",
                "tts_speed": 1.2,
            }
        )
        calls: list[tuple[str, dict]] = []

        def fake_gpt(value, out, **kwargs):
            calls.append((value, kwargs))
            Path(out).write_bytes(b"RIFF" + b"0" * 2000)
            return Path(out)

        with mock.patch.object(tts, "synthesize_gpt_sovits", fake_gpt):
            path = service._synthesize_tts("こんにちは")
        self.assertTrue(path.is_file())
        self.assertEqual(calls[0][0], "こんにちは")
        self.assertEqual(calls[0][1]["base_url"], "http://127.0.0.1:9880")
        self.assertEqual(calls[0][1]["ref_audio"], "C:/voice/ref.wav")
        self.assertEqual(calls[0][1]["prompt_text"], "参考文字")
        self.assertEqual(calls[0][1]["text_lang"], "ja")
        self.assertEqual(calls[0][1]["speed"], 1.2)

    def test_tts_voices_swallows_error(self) -> None:
        api, service = self.make_service()
        with mock.patch.object(tts, "windows_voices", side_effect=RuntimeError("坏了")):
            self.assertEqual(service.tts_voices(), [])
        self.assertTrue(any("读取 Windows 语音失败" in item for item in api.logs))

    def test_send_tts_record(self) -> None:
        api, service = self.make_service()
        clip = make_audio(Path(tempfile.mkdtemp()))
        result: list[tuple[bool, str]] = []
        with mock.patch.object(service, "_synthesize_tts", lambda value: clip):
            service.send_tts(
                "private:10001", "读这句话", lambda ok, msg: result.append((ok, msg))
            )
            self.assertTrue(service.client.event.wait(2))
        conversation, segments = service.client.sent[-1]
        self.assertEqual(conversation, "private:10001")
        self.assertEqual(segments, [{"type": "record", "data": {"file": str(clip)}}])

    def test_send_tts_rejects_stranger(self) -> None:
        api, service = self.make_service()
        result: list[tuple[bool, str]] = []
        service.send_tts("private:22222", "你好", lambda ok, msg: result.append((ok, msg)))
        self.assertFalse(result[0][0])
        self.assertIn("白名单", result[0][1])
        self.assertEqual(service.client.sent, [])

    def test_send_tts_rejects_empty_text(self) -> None:
        api, service = self.make_service()
        result: list[tuple[bool, str]] = []
        service.send_tts("private:10001", "[face:1]", lambda ok, msg: result.append((ok, msg)))
        self.assertFalse(result[0][0])
        self.assertIn("为空", result[0][1])
        self.assertEqual(service.client.sent, [])

    def test_reply_with_voice_tag(self) -> None:
        api, service = self.make_service()
        clip = make_audio(Path(tempfile.mkdtemp()))
        api.reply = "给你听个声音[voice]"
        with mock.patch.object(service, "_synthesize_tts", lambda value: clip):
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
        self.assertEqual(records[0]["data"]["file"], str(clip))
        texts = [
            segment["data"]["text"]
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "text"
        ]
        self.assertIn("给你听个声音", texts)
        self.assertNotIn("[voice]", "".join(texts))

    def test_reply_without_tag_stays_silent(self) -> None:
        api, service = self.make_service()
        api.reply = "今天天气不错。"
        with mock.patch.object(service, "_synthesize_tts", lambda value: make_audio(Path(tempfile.mkdtemp()))):
            service._on_event_main(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 10001,
                    "self_id": 99999,
                    "message_id": 10,
                    "message": [{"type": "text", "data": {"text": "在吗"}}],
                }
            )
            time.sleep(0.2)
        records = [
            segment
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "record"
        ]
        self.assertEqual(records, [])

    def test_voice_command(self) -> None:
        api, service = self.make_service()
        clip = make_audio(Path(tempfile.mkdtemp()))
        with mock.patch.object(service, "_synthesize_tts", lambda value: clip):
            service._on_event_main(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 10001,
                    "self_id": 99999,
                    "message_id": 11,
                    "message": [{"type": "text", "data": {"text": "/voice 读这句话"}}],
                }
            )
            self.assertTrue(service.client.event.wait(2))
        records = [
            segment
            for _, segments in service.client.sent
            for segment in segments
            if segment.get("type") == "record"
        ]
        self.assertEqual(records, [{"type": "record", "data": {"file": str(clip)}}])


if __name__ == "__main__":
    unittest.main()
