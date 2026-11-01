from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.self_dev"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_self_dev.agent import Agent, extract_action  # noqa: E402
from lfelkeea_self_dev.llm import describe_config, load_llm_config  # noqa: E402
from lfelkeea_self_dev.paths import DevPaths  # noqa: E402
from lfelkeea_self_dev.session import SessionLog  # noqa: E402
from lfelkeea_self_dev.settings import normalize_settings  # noqa: E402
from lfelkeea_self_dev.tools import ToolBox  # noqa: E402


def make_box(settings=None, enable=None, approver=None, asker=None):
    root = Path(tempfile.mkdtemp())
    workspace = root / "workspace"
    dev_dir = root / "dev"
    plugins = root / "data" / "plugins"
    data = root / "data"
    for folder in (workspace, dev_dir, plugins, data, dev_dir / "drafts"):
        folder.mkdir(parents=True, exist_ok=True)
    box = ToolBox(
        workspace=workspace,
        dev_dir=dev_dir,
        plugins_dir=plugins,
        data_dir=data,
        references=PLUGIN / "references",
        settings=normalize_settings(settings or {"approval_mode": "auto"}),
        enable_plugin=enable,
        approver=approver,
        asker=asker,
        stop_event=threading.Event(),
    )
    return box, root


def manifest(plugin_id="demo.hello", **extra):
    data = {
        "id": plugin_id,
        "name": "演示插件",
        "version": "1.0.0",
        "api_version": 1,
        "entry": "main.py",
        "description": "演示",
        "author": "test",
        "permissions": ["pet.say"],
    }
    data.update(extra)
    return json.dumps(data, ensure_ascii=False)


class PathSandboxTests(unittest.TestCase):
    def test_rejects_escape_and_absolute(self):
        box, _root = make_box()
        for bad in ("../escape.txt", "C:/Windows/x.txt", "/etc/passwd", "..\\escape.txt"):
            result = box.run("write_file", {"path": bad, "content": "x"})
            self.assertTrue(result.startswith("错误"), (bad, result))

    def test_drafts_are_writable(self):
        box, root = make_box()
        result = box.run("write_file", {"path": "drafts/my_plugin/main.py", "content": "print(1)\n"})
        self.assertIn("已写入", result)
        self.assertTrue((root / "dev" / "drafts" / "my_plugin" / "main.py").is_file())
        read = box.run("read_file", {"path": "drafts/my_plugin/main.py"})
        self.assertIn("print(1)", read)


class FileToolTests(unittest.TestCase):
    def test_write_read_edit_and_backup(self):
        box, root = make_box()
        self.assertIn("已写入", box.run("write_file", {"path": "a.py", "content": "value = 1\nvalue = 2\n"}))
        read = box.run("read_file", {"path": "a.py"})
        self.assertIn("1\tvalue = 1", read)
        self.assertIn("2\tvalue = 2", read)

        ambiguous = box.run("edit_file", {"path": "a.py", "old_string": "value", "new_string": "x"})
        self.assertIn("出现了 2 次", ambiguous)

        edited = box.run(
            "edit_file",
            {"path": "a.py", "old_string": "value = 1", "new_string": "value = 10"},
        )
        self.assertIn("已修改", edited)
        self.assertIn("value = 10", (root / "workspace" / "a.py").read_text(encoding="utf-8"))

        backups = list((root / "dev" / ".backups" / "files").glob("*/*.py"))
        self.assertTrue(backups, "覆盖前应该生成备份")

    def test_edit_replace_all(self):
        box, _root = make_box()
        box.run("write_file", {"path": "a.txt", "content": "aa\naa\n"})
        result = box.run(
            "edit_file",
            {"path": "a.txt", "old_string": "aa", "new_string": "bb", "replace_all": True},
        )
        self.assertIn("替换 2 处", result)

    def test_empty_file(self):
        box, _root = make_box()
        box.run("write_file", {"path": "empty.txt", "content": ""})
        self.assertIn("空文件", box.run("read_file", {"path": "empty.txt"}))

    def test_glob_and_grep(self):
        box, _root = make_box()
        box.run("write_file", {"path": "a.py", "content": "needle = 1\n"})
        box.run("write_file", {"path": "sub/b.py", "content": "other\nneedle = 2\n"})
        found = box.run("glob", {"pattern": "**/*.py"})
        self.assertIn("a.py", found)
        self.assertIn("sub/b.py", found)
        hits = box.run("grep", {"pattern": "needle", "path": "."})
        self.assertIn("a.py:1", hits)
        self.assertIn("sub/b.py:2", hits)

    def test_check_python_reports_syntax_error(self):
        box, _root = make_box()
        box.run("write_file", {"path": "bad.py", "content": "def broken(:\n    pass\n"})
        result = box.run("check_python", {"path": "bad.py"})
        self.assertIn("语法错误", result)
        self.assertIn("bad.py", result)

    def test_check_python_passes(self):
        box, _root = make_box()
        box.run("write_file", {"path": "ok.py", "content": "def f():\n    return 1\n"})
        self.assertIn("语法检查通过", box.run("check_python", {"path": "ok.py"}))


class ShellTests(unittest.TestCase):
    def test_runs_command(self):
        box, _root = make_box()
        result = box.run("run_shell", {"command": 'python -c "print(123)"', "timeout": 30})
        self.assertIn("退出码 0", result)
        self.assertIn("123", result)

    def test_approval_denied(self):
        box, _root = make_box(
            settings={"approval_mode": "confirm_danger"},
            approver=lambda *_args: False,
        )
        result = box.run("run_shell", {"command": 'python -c "print(1)"'})
        self.assertIn("主人拒绝", result)

    def test_shell_off(self):
        box, _root = make_box(settings={"approval_mode": "auto", "shell_mode": "off"})
        result = box.run("run_shell", {"command": "echo hi"})
        self.assertTrue(result.startswith("错误"))


class PluginForgeTests(unittest.TestCase):
    def test_create_plugin_and_enable(self):
        calls = []

        def enable(plugin_id, flag):
            calls.append((plugin_id, flag))
            return True, "已启用（测试）"

        box, root = make_box(enable=enable)
        files = {
            "plugin.json": manifest(),
            "main.py": "def setup(api):\n    api.say('hi')\n",
            "README.md": "# demo\n",
        }
        result = box.run("create_plugin", {"plugin_id": "demo.hello", "files": files, "enable": True})
        self.assertIn("已创建插件 demo.hello", result)
        self.assertIn("已启用并重载", result)
        self.assertEqual(calls, [("demo.hello", True)])
        target = root / "data" / "plugins" / "demo.hello"
        self.assertTrue((target / "main.py").is_file())
        self.assertIn("data/plugins/demo.hello", box.changed_files)

    def test_create_plugin_requires_manifest(self):
        box, _root = make_box()
        result = box.run("create_plugin", {"plugin_id": "demo.x", "files": {"main.py": "x=1\n"}})
        self.assertIn("必须包含 plugin.json", result)

    def test_create_plugin_rejects_bad_id(self):
        box, _root = make_box()
        result = box.run("create_plugin", {"plugin_id": "Bad Id", "files": {"plugin.json": manifest("bad")}})
        self.assertTrue(result.startswith("错误"))

    def test_create_plugin_rejects_id_mismatch(self):
        box, _root = make_box()
        files = {"plugin.json": manifest("other.id"), "main.py": "x=1\n"}
        result = box.run("create_plugin", {"plugin_id": "demo.hello", "files": files})
        self.assertIn("必须和 plugin_id 完全一致", result)

    def test_create_plugin_rejects_syntax_error(self):
        box, _root = make_box()
        files = {"plugin.json": manifest(), "main.py": "def broken(:\n"}
        result = box.run("create_plugin", {"plugin_id": "demo.hello", "files": files})
        self.assertIn("语法错误", result)

    def test_self_modify_blocked_by_default(self):
        box, _root = make_box()
        files = {"plugin.json": manifest("lfelkeea.self_dev"), "main.py": "x=1\n"}
        result = box.run(
            "create_plugin",
            {"plugin_id": "lfelkeea.self_dev", "files": files, "enable": False},
        )
        self.assertIn("不允许修改自开发工坊自身", result)

    def test_auto_enable_setting_off(self):
        calls = []
        box, _root = make_box(
            settings={"approval_mode": "auto", "auto_enable_plugin": False},
            enable=lambda pid, flag: calls.append((pid, flag)) or (True, "ok"),
        )
        files = {"plugin.json": manifest(), "main.py": "x=1\n"}
        result = box.run("create_plugin", {"plugin_id": "demo.hello", "files": files, "enable": True})
        self.assertIn("关闭了自动启用", result)
        self.assertEqual(calls, [])

    def test_unknown_permission_warns(self):
        box, _root = make_box()
        files = {
            "plugin.json": manifest(permissions=["pet.say", "make.coffee"]),
            "main.py": "x=1\n",
        }
        result = box.run("create_plugin", {"plugin_id": "demo.hello", "files": files, "enable": False})
        self.assertIn("未识别的权限", result)


class ProtocolTests(unittest.TestCase):
    def test_extract_plain_json(self):
        action = extract_action('{"tool": "read_file", "args": {"path": "a.py"}}')
        self.assertEqual(action["tool"], "read_file")

    def test_extract_fenced_json_with_chatter(self):
        text = '好的，我先看看。\n```json\n{"tool": "list_dir", "args": {}}\n```\n'
        self.assertEqual(extract_action(text)["tool"], "list_dir")

    def test_extract_final(self):
        self.assertEqual(extract_action('{"final": "完成"}')["final"], "完成")

    def test_extract_none(self):
        self.assertIsNone(extract_action("完全没有 JSON"))

    def test_extract_braces_in_strings(self):
        text = '{"tool": "write_file", "args": {"path": "a.py", "content": "x = {1: 2}"}}'
        self.assertEqual(extract_action(text)["args"]["content"], "x = {1: 2}")


class AgentLoopTests(unittest.TestCase):
    def test_agent_runs_tools_then_final(self):
        box, root = make_box()
        script = [
            {"thought": "先写文件", "tool": "write_file", "args": {"path": "x.py", "content": "print('ok')\n"}},
            {"thought": "再检查", "tool": "check_python", "args": {"path": "x.py"}},
            {"final": "写好了，语法检查通过。"},
        ]
        calls = []

        def chat_fn(messages, max_tokens):
            calls.append(max_tokens)
            return json.dumps(script[len(calls) - 1], ensure_ascii=False), "stop"

        events = []
        agent = Agent(
            chat_fn=chat_fn,
            toolbox=box,
            system_prompt="s",
            user_prompt="u",
            emit=events.append,
            stop_event=threading.Event(),
            max_steps=6,
        )
        result = agent.run()
        self.assertTrue(result["ok"])
        self.assertEqual(result["steps"], 3)
        self.assertIn("语法检查通过", result["summary"])
        self.assertTrue((root / "workspace" / "x.py").is_file())
        kinds = [item["type"] for item in events]
        self.assertIn("tool", kinds)
        self.assertIn("result", kinds)

    def test_agent_retries_bad_protocol(self):
        box, _root = make_box()
        replies = iter(["这不是 JSON", '{"final": "这次好了"}'])

        def chat_fn(messages, max_tokens):
            return next(replies), "stop"

        events = []
        agent = Agent(
            chat_fn=chat_fn,
            toolbox=box,
            system_prompt="s",
            user_prompt="u",
            emit=events.append,
            stop_event=threading.Event(),
            max_steps=4,
        )
        result = agent.run()
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"], "这次好了")

    def test_agent_stops(self):
        box, _root = make_box()
        stop = threading.Event()
        stop.set()

        def chat_fn(messages, max_tokens):
            raise AssertionError("不应该调用模型")

        result = Agent(
            chat_fn=chat_fn,
            toolbox=box,
            system_prompt="s",
            user_prompt="u",
            emit=lambda _event: None,
            stop_event=stop,
            max_steps=4,
        ).run()
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stopped")


class ConfigTests(unittest.TestCase):
    def test_loads_ai_config_and_hides_key(self):
        root = Path(tempfile.mkdtemp())
        config = root / "config.json"
        config.write_text(
            json.dumps(
                {
                    "ai": {
                        "provider": "openai",
                        "model": "gpt-4o",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "super-secret-key",
                        "timeout": 30,
                    }
                }
            ),
            encoding="utf-8",
        )
        loaded = load_llm_config(config, max_tokens=2048, temperature=0.2)
        self.assertEqual(loaded.provider, "openai")
        self.assertEqual(loaded.model, "gpt-4o")
        self.assertEqual(loaded.max_tokens, 2048)
        self.assertEqual(loaded.api_key, "super-secret-key")
        description = json.dumps(describe_config(loaded), ensure_ascii=False)
        self.assertNotIn("super-secret-key", description)
        self.assertIn('"key_configured": true', description)

    def test_missing_config_falls_back_to_pollinations(self):
        loaded = load_llm_config(Path(tempfile.mkdtemp()) / "missing.json")
        self.assertEqual(loaded.provider, "pollinations")
        self.assertFalse(describe_config(loaded)["needs_key"])

    def test_settings_are_clamped(self):
        settings = normalize_settings(
            {
                "approval_mode": "nonsense",
                "shell_mode": "nonsense",
                "max_steps": 999,
                "max_tokens": 10,
                "temperature": 9,
            }
        )
        self.assertEqual(settings["approval_mode"], "confirm_danger")
        self.assertEqual(settings["shell_mode"], "auto")
        self.assertEqual(settings["max_steps"], 60)
        self.assertEqual(settings["max_tokens"], 512)
        self.assertEqual(settings["temperature"], 1.5)


class PathsTests(unittest.TestCase):
    def test_env_override(self):
        root = Path(tempfile.mkdtemp())
        previous = os.environ.get("LFELKEEA_DATA_DIR")
        os.environ["LFELKEEA_DATA_DIR"] = str(root)
        try:
            paths = DevPaths.discover()
            self.assertEqual(paths.data_dir, root.resolve())
            self.assertEqual(paths.workspace, root.resolve() / "dev" / "workspace")
            paths.ensure()
            self.assertTrue(paths.workspace.is_dir())
            self.assertTrue((paths.dev_dir / "README.md").is_file())
        finally:
            if previous is None:
                os.environ.pop("LFELKEEA_DATA_DIR", None)
            else:
                os.environ["LFELKEEA_DATA_DIR"] = previous


class SessionTests(unittest.TestCase):
    def test_log_redacts_secrets(self):
        root = Path(tempfile.mkdtemp())
        log = SessionLog(root, "做一个插件")
        log.write({"type": "tool", "args": {"api_key": "secret-value", "nested": {"token": "abc"}}})
        log.close()
        text = log.path.read_text(encoding="utf-8")
        self.assertNotIn("secret-value", text)
        self.assertNotIn("abc", text)
        self.assertIn("做一个插件", text)


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None and not isinstance(app, QApplication):
            # 进程里已经存在纯 QGuiApplication（别的测试先创建），
            # Qt 不允许再升级成 QApplication，构造 QWidget 会直接崩溃。
            raise unittest.SkipTest("当前进程只有 QGuiApplication，跳过界面冒烟测试")
        cls.app = app or QApplication([])

    def test_dialog_builds(self):
        from PySide6.QtCore import QObject, Signal

        from lfelkeea_self_dev.ui import DevStudioDialog

        root = Path(tempfile.mkdtemp())
        (root / "workspace").mkdir(parents=True)

        class FakeService(QObject):
            event = Signal(object)

            def __init__(self):
                super().__init__()
                self.ui = None
                self.running = False
                self.settings = normalize_settings({})
                self.paths = SimpleNamespace(workspace=root / "workspace", sessions=root)

            def describe(self):
                return {
                    "workspace": str(root / "workspace"),
                    "provider": "ollama",
                    "model": "qwen2.5:7b",
                    "endpoint": "http://127.0.0.1:11434/v1",
                    "needs_key": False,
                    "error": "",
                    "max_tokens": 4096,
                    "session": "",
                }

            def backlog(self):
                return []

            def start_task(self, _task):
                return True, "ok"

            def stop_task(self):
                return False

        service = FakeService()
        dialog = DevStudioDialog(service)
        try:
            self.assertEqual(dialog.windowTitle(), "LFELKEEA · 自开发工坊")
            self.assertIn("workspace", dialog.subtitle.text())
            self.assertIs(service.ui, dialog)
            self.assertEqual(dialog.model_label.text().splitlines()[0], "ollama / qwen2.5:7b")
        finally:
            dialog.close()
            dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
