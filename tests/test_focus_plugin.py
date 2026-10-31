from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.focus_guard" / "main.py"


class FakeAPI:
    version = 1

    def __init__(self, settings: dict) -> None:
        self.settings = settings
        self.said: list[str] = []
        self.actions: list[str] = []
        self.timers: list[tuple[int, object]] = []
        self.logs: list[str] = []
        self.storage: dict = {}
        self.saved = 0

    def say(self, text: str, duration: int = 5000) -> None:
        self.said.append(text)

    def action(self, name: str) -> None:
        self.actions.append(name)

    def after(self, delay_ms: int, callback) -> None:
        self.timers.append((delay_ms, callback))

    def save_storage(self) -> None:
        self.saved += 1

    def log(self, message: str) -> None:
        self.logs.append(message)


def load_plugin():
    spec = importlib.util.spec_from_file_location("focus_guard_plugin", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FocusPluginTests(unittest.TestCase):
    def test_setup_starts_first_focus(self) -> None:
        api = FakeAPI({})
        load_plugin().setup(api)
        self.assertEqual(len(api.said), 1)
        self.assertIn("50 分钟", api.said[0])
        self.assertEqual(api.actions, ["nod"])
        self.assertEqual(api.timers[0][0], 50 * 60 * 1000)

    def test_focus_and_break_cycle(self) -> None:
        api = FakeAPI({"focus_minutes": 25, "break_minutes": 3})
        load_plugin().setup(api)
        self.assertEqual(api.timers[0][0], 25 * 60 * 1000)

        api.timers[0][1]()
        self.assertIn("25 分钟", api.said[-1])
        self.assertEqual(api.actions[-1], "stretch")
        self.assertEqual(api.storage["count"], 1)
        self.assertEqual(api.timers[1][0], 3 * 60 * 1000)

        api.timers[1][1]()
        self.assertIn("休息结束", api.said[-1])
        self.assertEqual(api.actions[-1], "nod")
        self.assertEqual(api.timers[2][0], 25 * 60 * 1000)

    def test_bad_settings_fall_back(self) -> None:
        api = FakeAPI({"focus_minutes": "abc", "break_minutes": 999})
        load_plugin().setup(api)
        self.assertEqual(api.timers[0][0], 50 * 60 * 1000)
        self.assertEqual(api.timers[0][1] is not None, True)
        api.timers[0][1]()
        self.assertEqual(api.timers[1][0], 30 * 60 * 1000)


if __name__ == "__main__":
    unittest.main()
