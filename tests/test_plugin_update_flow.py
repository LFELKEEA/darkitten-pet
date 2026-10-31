from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from config import Config
from plugin_manager import PluginManager
from plugin_updater import PluginRelease


PLUGIN_ID = "lfelkeea.demo"


def make_plugin(folder: Path, version: str, marker: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "id": PLUGIN_ID,
                "name": "Demo",
                "version": version,
                "api_version": 1,
                "entry": "main.py",
                "permissions": [],
            }
        ),
        encoding="utf-8",
    )
    (folder / "main.py").write_text(
        "LOADED = " + json.dumps(marker) + "\n\ndef setup(api):\n    pass\n",
        encoding="utf-8",
    )


class FakePet:
    ACTION_SPECS: dict = {}

    def say(self, *_args, **_kwargs) -> None:
        pass

    def start_action(self, *_args, **_kwargs) -> None:
        pass


class FakeConversation:
    pass


class PluginUpdateFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, timeout: float = 6.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        self.app.processEvents()
        return predicate()

    def test_update_replaces_plugin_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            plugins_dir = data_dir / "plugins"
            make_plugin(plugins_dir / PLUGIN_ID, "1.0.0", "old")
            empty = root / "empty"
            empty.mkdir()

            config = Config(data_dir / "config.json")
            config.set("plugins", {"enabled": {PLUGIN_ID: True}, "settings": {}})

            with mock.patch("plugin_manager.resource_dir", return_value=empty):
                manager = PluginManager(
                    config,
                    FakePet(),
                    FakeConversation(),
                    data_dir=data_dir,
                    plugins_dir=plugins_dir,
                )
                manager.load_enabled()
                self.assertIn(PLUGIN_ID, manager._loaded)

                archive = root / "demo-2.0.0.zip"
                manifest = {
                    "id": PLUGIN_ID,
                    "name": "Demo",
                    "version": "2.0.0",
                    "api_version": 1,
                    "entry": "main.py",
                    "permissions": [],
                }
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr(f"{PLUGIN_ID}/plugin.json", json.dumps(manifest))
                    zf.writestr(
                        f"{PLUGIN_ID}/main.py",
                        "LOADED = 'new'\n\ndef setup(api):\n    pass\n",
                    )
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                manager._releases = {
                    PLUGIN_ID: PluginRelease(
                        id=PLUGIN_ID,
                        name="Demo",
                        version="2.0.0",
                        download_url=archive.as_uri(),
                        sha256=digest,
                        size=archive.stat().st_size,
                    )
                }

                results: list[tuple[str, str]] = []
                manager.plugin_updated.connect(
                    lambda pid, error: results.append((pid, error))
                )
                manager.update_plugins([PLUGIN_ID])
                self.assertTrue(
                    self._wait(lambda: bool(results)), "plugin update timed out"
                )
                self.assertEqual(results[0], (PLUGIN_ID, ""))

                info = manager.get(PLUGIN_ID)
                self.assertIsNotNone(info)
                assert info is not None
                self.assertEqual(info.version, "2.0.0")
                self.assertIn(PLUGIN_ID, manager._loaded)
                loaded = manager._loaded[PLUGIN_ID]
                self.assertEqual(getattr(loaded.module, "LOADED", ""), "new")
                backups = list(
                    (plugins_dir / ".state" / "backups").glob(f"{PLUGIN_ID}-*")
                )
                self.assertEqual(len(backups), 1)
                manager.shutdown()

    def test_update_failure_keeps_old_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            plugins_dir = data_dir / "plugins"
            make_plugin(plugins_dir / PLUGIN_ID, "1.0.0", "old")
            empty = root / "empty"
            empty.mkdir()
            config = Config(data_dir / "config.json")
            config.set("plugins", {"enabled": {PLUGIN_ID: True}, "settings": {}})
            with mock.patch("plugin_manager.resource_dir", return_value=empty):
                manager = PluginManager(
                    config,
                    FakePet(),
                    FakeConversation(),
                    data_dir=data_dir,
                    plugins_dir=plugins_dir,
                )
                manager.load_enabled()
                manager._releases = {
                    PLUGIN_ID: PluginRelease(
                        id=PLUGIN_ID,
                        name="Demo",
                        version="2.0.0",
                        download_url=(root / "missing.zip").as_uri(),
                        sha256="a" * 64,
                    )
                }
                results: list[tuple[str, str]] = []
                manager.plugin_updated.connect(
                    lambda pid, error: results.append((pid, error))
                )
                manager.update_plugins([PLUGIN_ID])
                self.assertTrue(self._wait(lambda: bool(results)))
                self.assertTrue(results[0][1])
                info = manager.get(PLUGIN_ID)
                assert info is not None
                self.assertEqual(info.version, "1.0.0")
                manager.shutdown()


if __name__ == "__main__":
    unittest.main()
