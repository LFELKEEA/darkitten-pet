from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import snowluma  # noqa: E402
from lfelkeea_qq_bridge import service as service_module  # noqa: E402
from lfelkeea_qq_bridge.service import normalize_settings  # noqa: E402
from lfelkeea_qq_bridge.snowluma import (  # noqa: E402
    SnowLumaManager,
    detect_snowluma_dir,
    url_endpoint,
)


class FakeProcess:
    instances: list["FakeProcess"] = []

    def __init__(self, folder: Path, node: str, log_path: Path) -> None:
        self.folder = folder
        self.node = node
        self.log_path = log_path
        self.pid = 4321
        self.alive = True
        self.stopped = False
        FakeProcess.instances.append(self)

    def start(self) -> int:
        return self.pid

    def is_alive(self) -> bool:
        return self.alive

    def exit_code(self) -> int:
        return 0

    def stop(self) -> None:
        self.stopped = True
        self.alive = False


class SnowLumaUtilityTests(unittest.TestCase):
    def test_url_endpoint(self) -> None:
        self.assertEqual(url_endpoint("http://127.0.0.1:3003"), ("127.0.0.1", 3003))
        self.assertEqual(url_endpoint("http://localhost"), ("localhost", 80))
        self.assertEqual(url_endpoint("https://127.0.0.1"), ("127.0.0.1", 443))

    def test_start_hides_console_for_respawned_child(self) -> None:
        if not snowluma.IS_WINDOWS:
            self.skipTest("Windows only")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            process = snowluma.SnowLumaProcess(
                folder, "node.exe", folder / "log.txt"
            )
            fake = SimpleNamespace(pid=1234, poll=lambda: None)
            with mock.patch.object(
                snowluma.subprocess, "Popen", return_value=fake
            ) as popen, mock.patch.object(process._job, "open", return_value=False):
                self.assertEqual(process.start(), 1234)
        flags = popen.call_args.kwargs["creationflags"]
        # DETACHED_PROCESS 会让 SnowLuma 内部 respawn 的 node 子进程弹出新窗口。
        self.assertTrue(flags & snowluma.CREATE_NO_WINDOW)
        self.assertFalse(flags & 0x00000008)

    def test_normalize_auto_start(self) -> None:
        settings = normalize_settings(
            {"auto_start_snowluma": "on", "snowluma_dir": '  "D:/SnowLuma"  '}
        )
        self.assertTrue(settings["auto_start_snowluma"])
        self.assertEqual(settings["snowluma_dir"], "D:/SnowLuma")
        defaults = normalize_settings({})
        self.assertTrue(defaults["auto_start_snowluma"])
        self.assertEqual(defaults["snowluma_dir"], "")
        self.assertTrue(defaults["vision_enabled"])

    def test_detect_snowluma_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "index.mjs").write_text("// test\n", encoding="utf-8")
            (folder / "node.exe").write_bytes(b"")
            found = detect_snowluma_dir(str(folder))
            self.assertIsNotNone(found)
            self.assertEqual(Path(found).resolve(), folder.resolve())


class SnowLumaManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeProcess.instances.clear()
        self.logs: list[str] = []
        self.state = Path(tempfile.mkdtemp())
        self.folder = self.state / "SnowLuma"
        self.folder.mkdir()
        (self.folder / "index.mjs").write_text("// test\n", encoding="utf-8")
        (self.folder / "node.exe").write_bytes(b"")
        self.manager = SnowLumaManager(self.state, self.logs.append)
        self.manager.configure(
            {
                "auto_start_snowluma": True,
                "snowluma_dir": str(self.folder),
                "snowluma_http": "http://127.0.0.1:3003",
            }
        )
        self.manager._timeout = 0.2
        self.manager._stopping = False

    def test_disabled_does_nothing(self) -> None:
        manager = SnowLumaManager(self.state, self.logs.append)
        manager.configure(
            {
                "auto_start_snowluma": False,
                "snowluma_dir": str(self.folder),
                "snowluma_http": "http://127.0.0.1:3003",
            }
        )
        with mock.patch.object(snowluma, "SnowLumaProcess", FakeProcess):
            manager.ensure_started()
        self.assertEqual(FakeProcess.instances, [])

    def test_external_instance_is_not_owned(self) -> None:
        with mock.patch.object(snowluma, "SnowLumaProcess", FakeProcess), mock.patch.object(
            snowluma, "port_open", return_value=True
        ):
            self.manager.ensure_started()
        self.assertEqual(FakeProcess.instances, [])
        status = self.manager.status()
        self.assertFalse(status["managed"])
        self.assertTrue(status["external"])
        self.assertIn("已经在运行", "".join(self.logs))

    def test_running_without_api_does_not_spawn(self) -> None:
        with mock.patch.object(snowluma, "SnowLumaProcess", FakeProcess), mock.patch.object(
            snowluma, "port_open", side_effect=[False, True]
        ), mock.patch.object(
            snowluma, "webui_endpoint", return_value=("127.0.0.1", 5099)
        ):
            self.manager.ensure_started()
        self.assertEqual(FakeProcess.instances, [])
        self.assertIn("还没有监听", "".join(self.logs))

    def test_spawn_and_stop_owned_process(self) -> None:
        with mock.patch.object(snowluma, "SnowLumaProcess", FakeProcess), mock.patch.object(
            snowluma, "port_open", side_effect=[False, False, False, True]
        ), mock.patch.object(
            snowluma, "webui_endpoint", return_value=("127.0.0.1", 5099)
        ):
            self.manager.ensure_started()
        self.assertEqual(len(FakeProcess.instances), 1)
        process = FakeProcess.instances[0]
        self.assertFalse(process.stopped)
        status = self.manager.status()
        self.assertTrue(status["managed"])
        self.assertEqual(status["pid"], 4321)

        self.manager.stop()
        self.assertTrue(process.stopped)
        self.assertIn("已关闭", "".join(self.logs))
        self.assertFalse(self.manager.status()["managed"])

    def test_takeover_ends_external_node_and_restarts(self) -> None:
        with mock.patch.object(
            snowluma, "pid_listening_on", return_value=1234
        ), mock.patch.object(
            snowluma, "process_image_name", return_value="node.exe"
        ), mock.patch.object(
            snowluma, "terminate_process", return_value=True
        ) as killer, mock.patch.object(
            snowluma, "port_open", return_value=False
        ), mock.patch.object(
            snowluma, "webui_endpoint", return_value=("127.0.0.1", 5099)
        ), mock.patch.object(
            self.manager, "start_async"
        ) as starter:
            message = self.manager.takeover()
        killer.assert_called_once_with(1234)
        starter.assert_called_once_with(force=True)
        self.assertIn("重新启动", message)

    def test_takeover_refuses_other_process(self) -> None:
        with mock.patch.object(
            snowluma, "pid_listening_on", return_value=4321
        ), mock.patch.object(
            snowluma, "process_image_name", return_value="other.exe"
        ), mock.patch.object(
            snowluma, "terminate_process"
        ) as killer, mock.patch.object(
            snowluma, "webui_endpoint", return_value=("127.0.0.1", 5099)
        ):
            message = self.manager.takeover()
        killer.assert_not_called()
        self.assertIn("没有继续操作", message)
        self.assertFalse(self.manager._stopping)


class FakeManager:
    def __init__(self, *args, **kwargs) -> None:
        self.configures: list[dict] = []
        self.starts = 0
        self.stops = 0

    def configure(self, settings: dict, http: str | None = None) -> None:
        self.configures.append(dict(settings))

    def start_async(self) -> None:
        self.starts += 1

    def stop(self) -> None:
        self.stops += 1

    def status(self) -> dict:
        return {"managed": False, "pid": 0, "alive": False, "external": False}


class FakeAPI:
    version = 1

    def __init__(self) -> None:
        self.settings: dict = {}
        self.saved: list[dict] = []
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=Path(tempfile.mkdtemp()))

    def say(self, message: str, duration: int = 5000) -> None:
        pass

    def log(self, message: str) -> None:
        pass

    def save_settings(self, values: dict) -> None:
        self.saved.append(dict(values))
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        pass


class ServiceWiringTests(unittest.TestCase):
    def test_apply_settings_starts_and_stops_manager(self) -> None:
        manager = FakeManager()
        with mock.patch.object(
            service_module, "SnowLumaManager", return_value=manager
        ):
            service = service_module.QQBridgeService(FakeAPI())
        result = service.apply_settings(
            {"auto_start_snowluma": True, "snowluma_dir": "D:/SnowLuma"}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(manager.starts, 1)
        self.assertEqual(manager.configures[-1]["snowluma_dir"], "D:/SnowLuma")

        result = service.apply_settings({"auto_start_snowluma": False})
        self.assertTrue(result["ok"])
        # 第一次保存时目录从空变成 D:/SnowLuma，会先停一次旧实例（此时没有）。
        self.assertEqual(manager.stops, 2)

    def test_first_run_enables_auto_start_and_writes_backup(self) -> None:
        api = FakeAPI()
        with mock.patch.object(
            service_module, "SnowLumaManager", return_value=FakeManager()
        ):
            service = service_module.QQBridgeService(api)
        self.assertTrue(service.settings["auto_start_snowluma"])
        local = json.loads(
            (service.state_root / "settings.json").read_text(encoding="utf-8")
        )
        self.assertTrue(local["auto_start_snowluma"])

    def test_local_backup_wins_over_host_default(self) -> None:
        api = FakeAPI()
        state = Path(tempfile.mkdtemp())
        api.manager = SimpleNamespace(state_dir=state)
        with mock.patch.object(
            service_module, "SnowLumaManager", return_value=FakeManager()
        ):
            service = service_module.QQBridgeService(api)
            service.apply_settings(
                {"auto_start_snowluma": False, "snowluma_dir": "D:/SnowLuma"}
            )
            # 模拟桌宠退出时用内存里的旧配置回写，把开关又写成 true。
            api.settings["auto_start_snowluma"] = True
            api.settings["snowluma_dir"] = ""
            again = service_module.QQBridgeService(api)
        self.assertFalse(again.settings["auto_start_snowluma"])
        self.assertEqual(again.settings["snowluma_dir"], "D:/SnowLuma")

    def test_start_configure_and_start_when_enabled(self) -> None:
        manager = FakeManager()
        api = FakeAPI()
        api.settings.update(
            {
                "auto_start_snowluma": True,
                "snowluma_http": "http://127.0.0.1:3003",
            }
        )
        with mock.patch.object(
            service_module, "SnowLumaManager", return_value=manager
        ):
            service = service_module.QQBridgeService(api)

        class FakeServer:
            def __init__(self, *args, **kwargs) -> None:
                self.bound_port = 9999

            def start(self) -> int:
                return self.bound_port

            def stop(self) -> None:
                pass

        with mock.patch.object(service_module, "EventServer", FakeServer), mock.patch.object(
            service, "check_async"
        ):
            service.start()
        self.assertEqual(manager.starts, 1)
        self.assertEqual(manager.configures[-1]["auto_start_snowluma"], True)
        service.stop()
        self.assertEqual(manager.stops, 1)


if __name__ == "__main__":
    unittest.main()
