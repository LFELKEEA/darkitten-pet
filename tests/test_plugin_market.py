from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from config import Config
from plugin_manager import PluginManager
from plugin_market import MarketplaceDialog

PLUGIN_ID = "lfelkeea.market_demo"


def build_market_zip(
    folder: Path, version: str = "1.0.0", code: str | None = None
) -> Path:
    path = folder / f"{PLUGIN_ID}-{version}.zip"
    manifest = {
        "id": PLUGIN_ID,
        "name": "Market Demo",
        "version": version,
        "api_version": 1,
        "entry": "main.py",
        "permissions": ["pet.say"],
    }
    source = code if code is not None else (
        f"VALUE = {version!r}\n\ndef setup(api):\n    pass\n"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{PLUGIN_ID}/plugin.json", json.dumps(manifest))
        archive.writestr(f"{PLUGIN_ID}/main.py", source)
    return path


def write_registry(
    folder: Path,
    archive: Path,
    sha256: str | None = None,
    version: str = "1.0.0",
) -> Path:
    data = archive.read_bytes()
    entry = {
        "id": PLUGIN_ID,
        "name": "Market Demo",
        "version": version,
        "file": archive.name,
        "sha256": sha256 or hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "description": "市场测试插件",
        "author": "tester",
        "permissions": ["pet.say"],
        "requires": [{"name": "SnowLuma", "note": "自行安装"}],
        "featured": True,
        "bundled": False,
    }
    path = folder / "plugins.json"
    path.write_text(
        json.dumps({"api_version": 1, "plugins": [entry]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class FakePet:
    ACTION_SPECS: dict = {}

    def say(self, *_args, **_kwargs) -> None:
        pass

    def start_action(self, *_args, **_kwargs) -> None:
        pass


class FakeConversation:
    pass


class MarketplaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data_dir = self.root / "data"
        self.plugins_dir = self.data_dir / "plugins"
        self.plugins_dir.mkdir(parents=True)
        self.empty = self.root / "empty"
        self.empty.mkdir()

        patcher = mock.patch("plugin_manager.resource_dir", return_value=self.empty)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.config = Config(self.data_dir / "config.json")
        self.manager = PluginManager(
            self.config,
            FakePet(),
            FakeConversation(),
            data_dir=self.data_dir,
            plugins_dir=self.plugins_dir,
        )
        self.addCleanup(self.manager.shutdown)
        self.archive = build_market_zip(self.root)
        self.registry = write_registry(self.root, self.archive)
        self.config.set("updates", {"plugin_registry": self.registry.as_uri()})

    def _wait(self, predicate, timeout: float = 6.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        self.app.processEvents()
        return predicate()

    def _load(self) -> tuple[dict, str]:
        results: list[tuple[dict, str]] = []
        self.manager.marketplace_loaded.connect(
            lambda releases, error: results.append((releases, error))
        )
        self.assertTrue(self.manager.load_marketplace())
        self.assertTrue(self._wait(lambda: bool(results)), "marketplace load timed out")
        return results[0]

    def test_market_lists_and_installs(self) -> None:
        releases, error = self._load()
        self.assertEqual(error, "")
        self.assertIn(PLUGIN_ID, releases)

        release = self.manager.marketplace_release(PLUGIN_ID)
        assert release is not None
        self.assertEqual(release.name, "Market Demo")
        self.assertEqual(release.author, "tester")
        self.assertEqual(release.permissions, ["pet.say"])
        self.assertTrue(release.featured)
        self.assertEqual(release.requires[0]["name"], "SnowLuma")
        self.assertEqual([item.id for item in self.manager.marketplace_releases()], [PLUGIN_ID])

        results: list[tuple[str, str]] = []
        self.manager.plugin_installed.connect(
            lambda plugin_id, error: results.append((plugin_id, error))
        )
        self.assertTrue(self.manager.install_from_market(PLUGIN_ID))
        self.assertTrue(self._wait(lambda: bool(results)), "install timed out")
        self.assertEqual(results[0], (PLUGIN_ID, ""))
        self.assertEqual(self.manager.installing_plugin, "")

        info = self.manager.get(PLUGIN_ID)
        assert info is not None
        self.assertEqual(info.version, "1.0.0")
        self.assertFalse(info.enabled)
        self.assertNotIn(PLUGIN_ID, self.manager._loaded)
        self.assertTrue((self.plugins_dir / PLUGIN_ID / "plugin.json").is_file())
        self.assertIsNotNone(self.manager.marketplace_release(PLUGIN_ID))

        self.assertTrue(self.manager.set_enabled(PLUGIN_ID, True))
        self.assertIn(PLUGIN_ID, self.manager._loaded)
        self.assertTrue(self.manager.get(PLUGIN_ID).enabled)

    def test_market_update_replaces_and_keeps_enabled(self) -> None:
        self._load()
        results: list[tuple[str, str]] = []
        self.manager.plugin_installed.connect(
            lambda plugin_id, error: results.append((plugin_id, error))
        )
        self.assertTrue(self.manager.install_from_market(PLUGIN_ID))
        self.assertTrue(self._wait(lambda: bool(results)), "install timed out")
        self.assertEqual(results[0], (PLUGIN_ID, ""))
        self.assertTrue(self.manager.set_enabled(PLUGIN_ID, True))

        archive = build_market_zip(self.root, version="2.0.0")
        self.registry = write_registry(self.root, archive, version="2.0.0")
        self.config.set("updates", {"plugin_registry": self.registry.as_uri()})
        _releases, error = self._load()
        self.assertEqual(error, "")

        results.clear()
        self.assertTrue(self.manager.install_from_market(PLUGIN_ID))
        self.assertTrue(self._wait(lambda: bool(results)), "update timed out")
        self.assertEqual(results[0], (PLUGIN_ID, ""))
        info = self.manager.get(PLUGIN_ID)
        assert info is not None
        self.assertEqual(info.version, "2.0.0")
        self.assertTrue(info.enabled)
        self.assertIn(PLUGIN_ID, self.manager._loaded)
        self.assertEqual(
            getattr(self.manager._loaded[PLUGIN_ID].module, "VALUE", ""), "2.0.0"
        )
        backups = list(
            (self.plugins_dir / ".state" / "backups").glob(f"{PLUGIN_ID}-*")
        )
        self.assertEqual(len(backups), 1)

    def test_bad_sha_does_not_install(self) -> None:
        self.registry = write_registry(self.root, self.archive, sha256="f" * 64)
        self.config.set("updates", {"plugin_registry": self.registry.as_uri()})
        _releases, error = self._load()
        self.assertEqual(error, "")

        results: list[tuple[str, str]] = []
        self.manager.plugin_installed.connect(
            lambda plugin_id, error: results.append((plugin_id, error))
        )
        self.assertTrue(self.manager.install_from_market(PLUGIN_ID))
        self.assertTrue(self._wait(lambda: bool(results)), "install timed out")
        self.assertEqual(results[0][0], PLUGIN_ID)
        self.assertTrue(results[0][1])
        self.assertFalse((self.plugins_dir / PLUGIN_ID).exists())
        self.assertEqual(self.manager.installing_plugin, "")
        self.assertIsNotNone(self.manager.marketplace_release(PLUGIN_ID))

    def test_rejects_second_install(self) -> None:
        self._load()
        self.manager._installing = "other.plugin"
        try:
            self.assertFalse(self.manager.install_from_market(PLUGIN_ID))
        finally:
            self.manager._installing = ""

    def test_plugin_dialog_opens_market(self) -> None:
        self._load()
        from plugins_ui import PluginDialog

        dialog = PluginDialog(self.manager)
        self.addCleanup(dialog.deleteLater)
        try:
            dialog._open_market()
            self.assertIsNotNone(dialog.market_dialog)
            self.assertEqual(
                dialog.market_dialog.windowTitle(), "LFELKEEA · 插件市场"
            )
        finally:
            if dialog.market_dialog is not None:
                dialog.market_dialog.close()
            dialog.close()

    def test_market_dialog_install_flow(self) -> None:
        self._load()
        dialog = MarketplaceDialog(self.manager)
        self.addCleanup(dialog.deleteLater)
        try:
            self.assertEqual(dialog.windowTitle(), "LFELKEEA · 插件市场")
            self.assertIn("1 个插件", dialog.summary.text())
            self.assertIn(PLUGIN_ID, dialog._buttons)
            self.assertEqual(dialog._buttons[PLUGIN_ID].text(), "下载安装")

            dialog._install(PLUGIN_ID)
            self.assertTrue(
                self._wait(
                    lambda: PLUGIN_ID in dialog._buttons
                    and dialog._buttons[PLUGIN_ID].text() == "启用"
                ),
                "dialog did not switch to installed state",
            )
            self.assertIn("已安装", dialog.status.text())
            self.assertIsNotNone(self.manager.get(PLUGIN_ID))

            dialog._enable(PLUGIN_ID)
            self.assertTrue(self.manager.get(PLUGIN_ID).enabled)
            self.assertEqual(dialog._buttons[PLUGIN_ID].text(), "已启用")
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
