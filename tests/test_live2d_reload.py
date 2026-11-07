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

import live2d_model
from live2d_model import Live2DController, _alpha_bounds

APP = QApplication.instance() or QApplication([])


class AlphaBoundsTests(unittest.TestCase):
    def test_empty_frame_does_not_lock_full_canvas(self) -> None:
        # 新模型刚建好时第一帧可能全透明，此时不能把整块画布当成内容边界。
        raw = bytes(32 * 32 * 4)
        self.assertEqual(_alpha_bounds(raw, 32, 32), (0, 0, 0, 0))

    def test_opaque_pixel_gets_padded_bounds(self) -> None:
        width = height = 40
        raw = bytearray(width * height * 4)
        x, y = 20, 20
        raw[(y * width + x) * 4 + 3] = 255
        left, bottom, w, h = _alpha_bounds(bytes(raw), width, height)
        self.assertGreater(w, 0)
        self.assertGreater(h, 0)
        self.assertLessEqual(left + w, width)
        self.assertLessEqual(bottom + h, height)
        self.assertLessEqual(left, 20)
        self.assertLessEqual(bottom, 20)


class FakeThread:
    def __init__(self) -> None:
        self.commands: list[tuple[str, object]] = []

    def isRunning(self) -> bool:  # noqa: N802 - 对齐 QThread 接口
        return True

    def push(self, name: str, payload: object) -> None:
        self.commands.append((name, payload))


class DetachTests(unittest.TestCase):
    def test_detach_keeps_render_thread_alive(self) -> None:
        controller = Live2DController()
        thread = FakeThread()
        controller._thread = thread
        controller._loaded = True
        controller._info = {"canvas": (600, 900)}
        controller._owner = "plugin.test"

        self.assertTrue(controller.detach())

        self.assertIs(controller._thread, thread)
        self.assertFalse(controller.is_active)
        self.assertFalse(controller._loaded)
        self.assertTrue(controller._detached)
        self.assertEqual(thread.commands, [("detach", None)])
        self.assertEqual(controller.owner, "")

    def test_load_after_detach_reuses_thread(self) -> None:
        controller = Live2DController()
        thread = FakeThread()
        controller._thread = thread
        controller.detach()

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "m.model3.json"
            model.write_text("{}", encoding="utf-8")
            with mock.patch.object(live2d_model, "runtime_available", return_value=(True, "")):
                self.assertTrue(controller.load(str(model), owner="plugin.test"))

        self.assertIs(controller._thread, thread)
        self.assertFalse(controller._detached)
        self.assertEqual(thread.commands[-1][0], "load")
        self.assertEqual(controller.owner, "plugin.test")


if __name__ == "__main__":
    unittest.main()
