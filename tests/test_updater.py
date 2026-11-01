from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import updater  # noqa: E402
from version import APP_VERSION  # noqa: E402


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_manifest(folder: Path, payload: dict) -> Path:
    path = folder / "update.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def build_pet_zip(folder: Path, version: str = "9.9.9") -> Path:
    path = folder / "LFELKEEA.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("LFELKEEA/LFELKEEA.exe", b"MZ" + b"\x00" * 64)
        archive.writestr(
            "LFELKEEA/_internal/version.json", json.dumps({"version": version})
        )
        archive.writestr("LFELKEEA/_internal/app.py", "print('hi')")
    return path


class VersionTests(unittest.TestCase):
    def test_parse_and_compare(self) -> None:
        self.assertEqual(updater.parse_version("v1.2"), (1, 2, 0))
        self.assertTrue(updater.is_newer("1.2.0", "1.1.9"))
        self.assertFalse(updater.is_newer("1.1.1", "1.1.1"))
        with self.assertRaises(updater.UpdateError):
            updater.parse_version("abc")

    def test_release_from_pet_section(self) -> None:
        release = updater.release_from_manifest(
            {
                "pet": {
                    "version": "1.2.0",
                    "notes": ["a", "- b"],
                    "download_url": "downloads/pet.zip",
                    "sha256": "A" * 64,
                    "size": 123,
                }
            },
            "https://example.com/update.json",
        )
        self.assertEqual(release.version, "1.2.0")
        self.assertEqual(release.download_url, "https://example.com/downloads/pet.zip")
        self.assertEqual(release.sha256, "a" * 64)
        self.assertEqual(release.notes, ["a", "b"])
        self.assertEqual(release.size_text, "123 B")

    def test_release_from_standalone_manifest(self) -> None:
        release = updater.release_from_manifest(
            {"version": "2.0.0", "download_url": "https://x/pet.zip"},
            "https://x/pet_release.json",
        )
        self.assertEqual(release.version, "2.0.0")

    def test_invalid_digest(self) -> None:
        with self.assertRaises(updater.UpdateError):
            updater.release_from_manifest({"version": "1.0.0", "sha256": "xyz"})


class CheckTests(unittest.TestCase):
    def test_check_for_update_with_local_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            path = write_manifest(
                folder,
                {
                    "pet": {
                        "version": "9.9.9",
                        "download_url": "pet.zip",
                        "sha256": "b" * 64,
                        "size": 10,
                    }
                },
            )
            release = updater.check_for_update("1.2.0", url=path.as_uri())
            self.assertIsNotNone(release)
            self.assertEqual(release.version, "9.9.9")
            self.assertTrue(release.download_url.endswith("/pet.zip"))

    def test_check_returns_none_when_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_manifest(Path(tmp), {"pet": {"version": "1.2.0"}})
            self.assertIsNone(updater.check_for_update("1.2.0", url=path.as_uri()))


class DownloadTests(unittest.TestCase):
    def test_download_and_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source_dir = folder / "payload"
            source_dir.mkdir()
            archive = build_pet_zip(source_dir, "9.9.9")
            data = archive.read_bytes()
            release = updater.PetRelease(
                version="9.9.9",
                download_url=archive.as_uri(),
                sha256=sha256_bytes(data),
                size=len(data),
            )
            dest = updater.download_update(release, folder / "updates")
            self.assertEqual(dest.read_bytes(), data)
            payload = updater.prepare_update(dest, folder / "updates", "9.9.9")
            self.assertTrue((payload / "LFELKEEA.exe").is_file())
            self.assertTrue((payload / "_internal" / "version.json").is_file())

    def test_download_rejects_bad_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = build_pet_zip(folder, "9.9.9")
            release = updater.PetRelease(
                version="9.9.9",
                download_url=archive.as_uri(),
                sha256="c" * 64,
                size=archive.stat().st_size,
            )
            with self.assertRaises(updater.UpdateError):
                updater.download_update(release, folder / "updates")
            self.assertFalse((folder / "updates" / "LFELKEEA-9.9.9.zip").exists())

    def test_prepare_rejects_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = build_pet_zip(folder, "9.9.9")
            with self.assertRaises(updater.UpdateError):
                updater.prepare_update(archive, folder / "updates", "9.9.8")

    def test_safe_extract_blocks_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            archive = folder / "evil.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../evil.txt", "x")
            with self.assertRaises(updater.UpdateError):
                updater._safe_extract(archive, folder / "out")

    def test_apply_script_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = updater.write_apply_script(Path(tmp))
            self.assertTrue(script.is_file())
            text = script.read_text(encoding="utf-8")
            self.assertIn("LFELKEEA.exe", text)
            self.assertIn("_internal", text)
            self.assertIn("data\\updates", text)
            self.assertTrue(text.isascii(), "PowerShell 5.1 会按 ANSI 读取无 BOM 脚本")


class AppVersionTests(unittest.TestCase):
    def test_app_version_shape(self) -> None:
        self.assertRegex(APP_VERSION, r"^\d+\.\d+\.\d+$")


class ApplyTests(unittest.TestCase):
    def test_apply_flags_do_not_detach(self) -> None:
        flags = updater.apply_flags()
        self.assertEqual(flags & subprocess.CREATE_NO_WINDOW, subprocess.CREATE_NO_WINDOW)
        self.assertFalse(flags & 0x00000008)  # DETACHED_PROCESS

    def test_schedule_apply_replaces_files(self) -> None:
        if platform.system() != "Windows":
            self.skipTest("自动替换只支持 Windows")
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            install = base / "install"
            (install / "_internal").mkdir(parents=True)
            (install / "LFELKEEA.exe").write_bytes(b"MZold")
            (install / "_internal" / "version.json").write_text(
                json.dumps({"version": "1.0.0"}), encoding="utf-8"
            )
            payload = base / "payload"
            (payload / "_internal").mkdir(parents=True)
            (payload / "LFELKEEA.exe").write_bytes(b"MZnew")
            (payload / "_internal" / "version.json").write_text(
                json.dumps({"version": "9.9.9"}), encoding="utf-8"
            )

            # 模拟运行中的桌宠占着 exe：脚本必须等这个 PID 退出后再替换。
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import time; f = open(r'%s', 'rb'); time.sleep(2)"
                    % (install / "LFELKEEA.exe"),
                ]
            )
            self.addCleanup(holder.wait, 20)
            updater.schedule_apply(payload, install, restart=False, pid=holder.pid)

            log_path = updater.updates_dir(install) / "apply.log"
            deadline = time.time() + 25
            while time.time() < deadline:
                if "update applied" in updater.read_apply_log(install, limit=10_000):
                    break
                time.sleep(0.2)
            self.assertIn("update applied", updater.read_apply_log(install, limit=10_000))
            self.assertTrue(log_path.is_file())
            self.assertEqual((install / "LFELKEEA.exe").read_bytes(), b"MZnew")
            packed = json.loads(
                (install / "_internal" / "version.json").read_text(encoding="utf-8")
            )
            self.assertEqual(packed["version"], "9.9.9")
            self.assertFalse(payload.exists())
            backups = list((install / "data" / "updates").glob("backup-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "LFELKEEA.exe").read_bytes(), b"MZold")


if __name__ == "__main__":
    unittest.main()
