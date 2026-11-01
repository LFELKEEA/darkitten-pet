from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QGuiApplication

import voice
from conversation import Conversation
from voice import VoiceChatController, VoiceError

APP = QGuiApplication.instance() or QGuiApplication([])


def wait_until(check, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        APP.processEvents()
        if check():
            return True
        time.sleep(0.01)
    APP.processEvents()
    return bool(check())


class FakePet:
    def __init__(self) -> None:
        self.said: list[str] = []
        self.bubble = SimpleNamespace(hide_now=lambda: None)
        self._last_interaction = 0.0
        self.bounces = 0

    def say(self, text: str, duration: int = 5000) -> None:
        self.said.append(text)

    def _trigger_bounce(self) -> None:
        self.bounces += 1


class FakeRecorder:
    def __init__(self, *, open_delay: float = 0.0, stop_delay: float = 0.0, wav: Path | None = None):
        self.open_delay = open_delay
        self.stop_delay = stop_delay
        self.wav = wav
        self.open_thread = ""
        self.stop_thread = ""
        self.closed = False
        self.stopped = False

    def start(self) -> None:
        self.open_thread = threading.current_thread().name
        time.sleep(self.open_delay)

    def stop(self) -> Path | None:
        self.stop_thread = threading.current_thread().name
        self.stopped = True
        time.sleep(self.stop_delay)
        return self.wav

    def cancel(self) -> None:
        self.closed = True


class FakeSpeechRequest:
    instances: list["FakeSpeechRequest"] = []

    def __init__(self, wav_path: Path) -> None:
        self.wav_path = Path(wav_path)
        self.started = False
        self.signals = SimpleNamespace(ok=SimpleNamespace(connect=lambda *_: None),
                                       fail=SimpleNamespace(connect=lambda *_: None))
        FakeSpeechRequest.instances.append(self)

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        pass


class ControllerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.recorders: list[FakeRecorder] = []
        self.factory_error: Exception | None = None
        self.pet = FakePet()
        self.controller = VoiceChatController(
            SimpleNamespace(ai={}), self.pet, conversation=Conversation(4)
        )
        self.controller._recorder_factory = self._make_recorder
        self._temp_files: list[Path] = []

    def tearDown(self) -> None:
        self.controller.shutdown()
        for path in self._temp_files:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _make_recorder(self) -> FakeRecorder:
        if self.factory_error is not None:
            raise self.factory_error
        recorder = FakeRecorder(**self.recorder_options)
        self.recorders.append(recorder)
        return recorder

    recorder_options: dict = {}

    def temp_wav(self) -> Path:
        handle, name = tempfile.mkstemp(prefix="voice_test_", suffix=".wav")
        os.close(handle)
        path = Path(name)
        self._temp_files.append(path)
        return path


class PrimeTests(ControllerHarness):
    recorder_options = {"open_delay": 0.25}

    def test_prime_returns_immediately(self) -> None:
        started = time.monotonic()
        self.controller.prime()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.05)
        self.assertEqual(self.controller._state, "priming")
        self.assertTrue(wait_until(lambda: self.controller._recorder is not None))
        self.assertEqual(self.recorders[0].open_thread, "pet-mic-open")

    def test_hold_waits_for_background_open(self) -> None:
        self.controller.prime()
        self.controller.start_listening()
        self.assertEqual(self.controller._state, "pending")
        self.assertTrue(wait_until(lambda: self.controller._state == "recording"))
        self.assertIn("我在听", "".join(self.pet.said))

    def test_release_before_ready_closes_recorder(self) -> None:
        self.controller.prime()
        self.controller.start_listening()
        self.controller.finish_listening()
        self.assertEqual(self.controller._state, "idle")
        self.assertTrue(wait_until(lambda: self.recorders and self.recorders[0].closed))
        self.assertNotIn("我在听", "".join(self.pet.said))


class StartFromIdleTests(ControllerHarness):
    recorder_options = {"open_delay": 0.1}

    def test_start_without_prime_opens_and_records(self) -> None:
        self.controller.start_listening()
        self.assertEqual(self.controller._state, "pending")
        self.assertTrue(wait_until(lambda: self.controller._state == "recording"))
        self.assertIn("我在听", "".join(self.pet.said))


class FailureTests(ControllerHarness):
    recorder_options = {}

    def test_hold_reports_microphone_error(self) -> None:
        self.factory_error = VoiceError("麦克风打不开：没有设备")
        self.controller.start_listening()
        self.assertTrue(wait_until(lambda: any("请在 Windows 设置里允许麦克风访问" in t for t in self.pet.said)))
        self.assertEqual(self.controller._state, "idle")

    def test_prime_failure_is_silent_and_backs_off(self) -> None:
        self.factory_error = VoiceError("麦克风打不开：没有设备")
        self.controller.prime()
        self.assertTrue(wait_until(lambda: self.controller._state == "idle"))
        self.assertEqual(self.pet.said, [])
        self.assertGreater(self.controller._prime_blocked_until, time.monotonic())
        self.controller.prime()
        self.assertEqual(len(self.recorders), 0)


class StopTests(ControllerHarness):
    recorder_options = {"open_delay": 0.0, "stop_delay": 0.2}

    def test_stop_runs_off_main_thread(self) -> None:
        original = voice.SpeechToTextRequest
        voice.SpeechToTextRequest = FakeSpeechRequest
        FakeSpeechRequest.instances.clear()
        try:
            wav = self.temp_wav()
            self.recorder_options = {"open_delay": 0.0, "stop_delay": 0.2, "wav": wav}
            self.controller.prime()
            self.controller.start_listening()
            self.assertTrue(wait_until(lambda: self.controller._state == "recording"))
            started = time.monotonic()
            self.controller.finish_listening()
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 0.05)
            self.assertTrue(wait_until(lambda: self.controller._state == "transcribing"))
            self.assertEqual(self.recorders[0].stop_thread, "pet-mic-stop")
            self.assertTrue(FakeSpeechRequest.instances[0].started)
        finally:
            voice.SpeechToTextRequest = original


if __name__ == "__main__":
    unittest.main()
