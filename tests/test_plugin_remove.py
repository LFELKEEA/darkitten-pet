from __future__ import annotations

import json
import os
import shutil
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

import plugin_manager as plugin_manager_module
from config import Config
from plugin_manager import PluginInfo, PluginManager

APP = QApplication.instance() or QApplication([])


class FakePet:
    def say(self, *args, **kwargs) -> None:
        pass

    def action(self, *args, **kwargs) -> None:
        pass


def write_plugin(
    root: Path,
    plugin_id: str,
    code: str = "def setup(api):\n    pass\n",
    permissions: list[str] | None = None,
) -> Path:
    folder = root / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": f"Demo {plugin_id}",
        "version": "1.0.0",
        "api_version": 1,
        "entry": "main.py",
        "permissions": permissions or [],
    }
    (folder / "plugin.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    (folder / "main.py").write_text(code, encoding="utf-8")
    return folder


class PluginRemoveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "data"
        self.plugins = self.data / "plugins"
        self.plugins.mkdir(parents=True)
        self.bundled = self.tmp / "bundled"
        (self.bundled / "plugins").mkdir(parents=True)
        self.trash = self.tmp / "trash"
        self.trash.mkdir()
        self.config = Config(path=self.data / "config.json")
        self.manager = PluginManager(
            self.config,
            FakePet(),
            None,
            data_dir=self.data,
            plugins_dir=self.plugins,
        )
        self._patches = [
            mock.patch.object(plugin_manager_module, "resource_dir", lambda: self.bundled),
            mock.patch.object(plugin_manager_module, "move_to_trash", self._trash),
        ]
        for patcher in self._patches:
            patcher.start()

    def tearDown(self) -> None:
        self.manager.shutdown()
        for patcher in reversed(self._patches):
            patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _trash(self, path: Path) -> tuple[bool, str]:
        target = self.trash / Path(path).name
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(path), str(target))
        return True, ""

    def _plugins_config(self) -> dict:
        return dict(self.config.get("plugins", {}) or {})

    def test_remove_disabled_plugin_cleans_config_and_state(self) -> None:
        folder = write_plugin(self.plugins, "demo.one")
        self.manager.discover()
        self.manager._save_enabled_map({"demo.one": True})
        self.manager.save_plugin_settings("demo.one", {"interval": 15})
        self.manager.save_storage("demo.one", {"count": 3})
        self.assertTrue((self.plugins / ".state" / "demo.one.json").is_file())

        ok, message = self.manager.remove_plugin("demo.one")

        self.assertTrue(ok, message)
        self.assertFalse(folder.exists())
        self.assertTrue((self.trash / "demo.one").is_dir())
        self.assertIsNone(self.manager.get("demo.one"))
        self.assertFalse((self.plugins / ".state" / "demo.one.json").exists())
        plugins = self._plugins_config()
        self.assertNotIn("demo.one", plugins.get("enabled", {}))
        self.assertNotIn("demo.one", plugins.get("settings", {}))
        self.assertIn("demo.one", plugins.get("removed", []))
        self.assertIn("回收站", message)

    def test_remove_running_plugin_stops_timers_and_context(self) -> None:
        code = (
            "def setup(api):\n"
            "    api.every(60000, lambda: None)\n"
            "    api.register_prompt_provider(lambda **kwargs: 'hello')\n"
        )
        write_plugin(
            self.plugins,
            "demo.running",
            code=code,
            permissions=["timer", "chat.context"],
        )
        self.manager.discover()
        self.assertTrue(self.manager.set_enabled("demo.running", True))
        loaded = self.manager._loaded["demo.running"]
        self.assertIn("demo.running", self.manager._prompt_providers)

        ok, message = self.manager.remove_plugin("demo.running")

        self.assertTrue(ok, message)
        self.assertNotIn("demo.running", self.manager._loaded)
        self.assertNotIn("demo.running", self.manager._prompt_providers)
        self.assertEqual(loaded.api._timers, [])
        self.assertFalse((self.plugins / "demo.running").exists())

    def test_failed_trash_restores_running_plugin(self) -> None:
        code = (
            "def setup(api):\n"
            "    api.register_prompt_provider(lambda **kwargs: 'hello')\n"
        )
        folder = write_plugin(
            self.plugins, "demo.locked", code=code, permissions=["chat.context"]
        )
        self.manager.discover()
        self.assertTrue(self.manager.set_enabled("demo.locked", True))

        with mock.patch.object(
            plugin_manager_module,
            "move_to_trash",
            lambda path: (False, "文件被占用。"),
        ):
            ok, message = self.manager.remove_plugin("demo.locked")

        self.assertFalse(ok)
        self.assertIn("文件被占用", message)
        self.assertTrue(folder.exists())
        self.assertIn("demo.locked", self.manager._loaded)
        self.assertIn("demo.locked", self.manager._prompt_providers)
        self.assertTrue(self.manager.get("demo.locked").enabled)

    def test_removed_bundled_plugin_is_not_copied_back(self) -> None:
        shipped = self.bundled / "plugins"
        write_plugin(shipped, "example.one")
        self.manager.discover()
        self.assertTrue((self.plugins / "example.one").is_dir())

        ok, message = self.manager.remove_plugin("example.one")
        self.assertTrue(ok, message)
        self.manager.discover()
        self.assertFalse((self.plugins / "example.one").exists())

        # 手动放回或从市场重装时，删除标记自动失效。
        shutil.copytree(shipped / "example.one", self.plugins / "example.one")
        self.manager.discover()
        self.assertTrue((self.plugins / "example.one").is_dir())
        self.assertNotIn("example.one", self._plugins_config().get("removed", []))

    def test_refuses_path_outside_plugins_dir(self) -> None:
        outside = self.tmp / "outside"
        write_plugin(self.tmp, "outside")
        info = PluginInfo(
            id="outside",
            name="Outside",
            version="1.0.0",
            api_version=1,
            entry="main.py",
            folder=outside,
        )
        self.manager._infos["outside"] = info

        ok, message = self.manager.remove_plugin("outside")

        self.assertFalse(ok)
        self.assertIn("直接子目录", message)
        self.assertTrue(outside.is_dir())

    def test_missing_plugin_returns_reason(self) -> None:
        ok, message = self.manager.remove_plugin("not.there")
        self.assertFalse(ok)
        self.assertIn("不存在", message)

    def test_refuses_while_installing_or_updating(self) -> None:
        folder = write_plugin(self.plugins, "demo.busy")
        self.manager.discover()

        self.manager._installing = "demo.busy"
        ok, message = self.manager.remove_plugin("demo.busy")
        self.assertFalse(ok)
        self.assertIn("正在安装", message)
        self.assertTrue(folder.is_dir())

        self.manager._installing = ""
        self.manager._updating_plugin = "demo.busy"
        ok, message = self.manager.remove_plugin("demo.busy")
        self.assertFalse(ok)
        self.assertIn("正在更新", message)
        self.assertTrue(folder.is_dir())


if __name__ == "__main__":
    unittest.main()
