from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import plugin_updater
from plugin_updater import PluginRelease


def build_plugin_zip(
    folder: Path,
    plugin_id: str = "lfelkeea.demo",
    version: str = "2.0.0",
    code: str = "VALUE = 'new'\n",
    root: str | None = None,
) -> Path:
    path = folder / f"{plugin_id}-{version}.zip"
    base = root if root is not None else plugin_id
    manifest = {
        "id": plugin_id,
        "name": "Demo",
        "version": version,
        "api_version": 1,
        "entry": "main.py",
        "permissions": [],
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{base}/plugin.json", json.dumps(manifest))
        archive.writestr(f"{base}/main.py", code)
    return path


class RegistryTests(unittest.TestCase):
    def test_releases_from_registry(self) -> None:
        data = {
            "plugins": [
                {
                    "id": "lfelkeea.demo",
                    "name": "Demo",
                    "version": "2.0.0",
                    "file": "plugins/demo-2.0.0.zip",
                    "sha256": "A" * 64,
                    "size": 100,
                },
                {"id": "bad", "version": "1.0.0"},
            ]
        }
        releases = plugin_updater.releases_from_registry(
            data, "https://example.com/plugins.json"
        )
        self.assertIn("lfelkeea.demo", releases)
        release = releases["lfelkeea.demo"]
        self.assertEqual(release.download_url, "https://example.com/plugins/demo-2.0.0.zip")
        self.assertEqual(release.sha256, "a" * 64)
        self.assertNotIn("bad", releases)

    def test_releases_parse_market_metadata(self) -> None:
        data = {
            "plugins": [
                {
                    "id": "lfelkeea.demo",
                    "name": "Demo",
                    "version": "2.0.0",
                    "file": "plugins/demo-2.0.0.zip",
                    "sha256": "B" * 64,
                    "size": 123,
                    "description": "演示插件",
                    "author": "tester",
                    "permissions": ["pet.say", "ai.chat"],
                    "requires": [
                        {"name": "SnowLuma", "version": "1.0", "note": "自行安装"},
                        "bad",
                    ],
                    "featured": True,
                    "bundled": False,
                }
            ]
        }
        release = plugin_updater.releases_from_registry(
            data, "https://example.com/plugins.json"
        )["lfelkeea.demo"]
        self.assertEqual(release.author, "tester")
        self.assertEqual(release.permissions, ["pet.say", "ai.chat"])
        self.assertTrue(release.featured)
        self.assertFalse(release.bundled)
        self.assertEqual(len(release.requires), 1)
        self.assertEqual(release.requires[0]["name"], "SnowLuma")
        self.assertEqual(release.requires[0]["note"], "自行安装")

    def test_check_updates_with_local_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            data = {
                "plugins": [
                    {
                        "id": "lfelkeea.demo",
                        "name": "Demo",
                        "version": "2.0.0",
                        "file": "demo.zip",
                        "sha256": "d" * 64,
                    },
                    {
                        "id": "lfelkeea.same",
                        "name": "Same",
                        "version": "1.0.0",
                        "file": "same.zip",
                        "sha256": "e" * 64,
                    },
                ]
            }
            registry = folder / "plugins.json"
            registry.write_text(json.dumps(data), encoding="utf-8")
            found = plugin_updater.check_updates(
                {"lfelkeea.demo": "1.0.0", "lfelkeea.same": "1.0.0"},
                url=registry.as_uri(),
            )
            self.assertEqual(list(found), ["lfelkeea.demo"])


class DownloadTests(unittest.TestCase):
    def test_download_and_verify(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = build_plugin_zip(folder)
            data = archive.read_bytes()
            release = PluginRelease(
                id="lfelkeea.demo",
                name="Demo",
                version="2.0.0",
                download_url=archive.as_uri(),
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
            )
            dest = plugin_updater.download_plugin(release, folder / "cache")
            self.assertEqual(dest.read_bytes(), data)

    def test_download_rejects_bad_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = build_plugin_zip(folder)
            release = PluginRelease(
                id="lfelkeea.demo",
                name="Demo",
                version="2.0.0",
                download_url=archive.as_uri(),
                sha256="f" * 64,
                size=archive.stat().st_size,
            )
            with self.assertRaises(plugin_updater.PluginUpdateError):
                plugin_updater.download_plugin(release, folder / "cache")


class InstallTests(unittest.TestCase):
    def _prepare_old(self, root: Path) -> Path:
        plugins = root / "plugins"
        target = plugins / "lfelkeea.demo"
        target.mkdir(parents=True)
        (target / "plugin.json").write_text(
            json.dumps(
                {
                    "id": "lfelkeea.demo",
                    "name": "Old",
                    "version": "1.0.0",
                    "entry": "main.py",
                }
            ),
            encoding="utf-8",
        )
        (target / "main.py").write_text("VALUE = 'old'\n", encoding="utf-8")
        state = plugins / ".state"
        state.mkdir(parents=True, exist_ok=True)
        (state / "lfelkeea.demo.json").write_text('{"keep": true}', encoding="utf-8")
        return plugins

    def test_install_replaces_and_keeps_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = self._prepare_old(root)
            archive = build_plugin_zip(root)
            target = plugin_updater.install_plugin(
                archive, plugins, "lfelkeea.demo", "2.0.0"
            )
            self.assertEqual(target, plugins / "lfelkeea.demo")
            manifest = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "2.0.0")
            self.assertIn("new", (target / "main.py").read_text(encoding="utf-8"))
            self.assertTrue((plugins / ".state" / "lfelkeea.demo.json").is_file())
            backups = list((plugins / ".state" / "backups").glob("lfelkeea.demo-*"))
            self.assertEqual(len(backups), 1)
            self.assertIn(
                "old", (backups[0] / "main.py").read_text(encoding="utf-8")
            )
            self.assertFalse((plugins.parent / "updates" / "plugin-staging").exists())

    def test_install_rejects_id_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = self._prepare_old(root)
            archive = build_plugin_zip(root, plugin_id="other.plugin")
            with self.assertRaises(plugin_updater.PluginUpdateError):
                plugin_updater.install_plugin(archive, plugins, "lfelkeea.demo")

    def test_install_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = self._prepare_old(root)
            archive = build_plugin_zip(root, version="2.0.0")
            with self.assertRaises(plugin_updater.PluginUpdateError):
                plugin_updater.install_plugin(
                    archive, plugins, "lfelkeea.demo", "2.0.1"
                )

    def test_install_rejects_unsafe_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = self._prepare_old(root)
            archive = root / "evil.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape.txt", "x")
            with self.assertRaises(plugin_updater.PluginUpdateError):
                plugin_updater.install_plugin(archive, plugins, "lfelkeea.demo")

    def test_install_rejects_missing_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugins = self._prepare_old(root)
            archive = root / "missing.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "lfelkeea.demo/plugin.json",
                    json.dumps(
                        {
                            "id": "lfelkeea.demo",
                            "name": "Demo",
                            "version": "2.0.0",
                            "entry": "main.py",
                        }
                    ),
                )
            with self.assertRaises(plugin_updater.PluginUpdateError):
                plugin_updater.install_plugin(archive, plugins, "lfelkeea.demo")


if __name__ == "__main__":
    unittest.main()
