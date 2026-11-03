from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.self_dev"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_self_dev import requirements as req_tools  # noqa: E402
from lfelkeea_self_dev.connectors import ConnectorStore  # noqa: E402
from lfelkeea_self_dev.envstore import EnvStore  # noqa: E402
from lfelkeea_self_dev.net import (  # noqa: E402
    Blob,
    NetworkError,
    resolve_env,
    set_env_provider,
)
from lfelkeea_self_dev.resource_util import ResourceError  # noqa: E402
from lfelkeea_self_dev.settings import normalize_settings  # noqa: E402
from lfelkeea_self_dev.skills import SkillStore  # noqa: E402
from lfelkeea_self_dev.tools import TOOL_SPECS, ToolBox  # noqa: E402


def temp_root() -> Path:
    return Path(tempfile.mkdtemp())


def make_box(settings=None, approver=None):
    root = temp_root()
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
        approver=approver,
        stop_event=threading.Event(),
    )
    return box, root


class EnvStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.store = EnvStore(self.root / "env.json")
        self.addCleanup(set_env_provider, None)

    def test_set_get_source_clear_and_public_masking(self) -> None:
        self.store.set("TIANXI_TEST_TOKEN", "super-secret", note="测试令牌")
        self.assertEqual(self.store.get("TIANXI_TEST_TOKEN"), "super-secret")
        self.assertEqual(self.store.source("TIANXI_TEST_TOKEN"), "local")
        self.assertTrue(self.store.is_configured("TIANXI_TEST_TOKEN"))

        public = self.store.public(["TIANXI_TEST_TOKEN", "TIANXI_TEST_MISSING"])
        text = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("super-secret", text)
        by_name = {item["name"]: item for item in public}
        self.assertTrue(by_name["TIANXI_TEST_TOKEN"]["configured"])
        self.assertEqual(by_name["TIANXI_TEST_TOKEN"]["masked"], "***")
        self.assertFalse(by_name["TIANXI_TEST_MISSING"]["configured"])
        self.assertEqual(by_name["TIANXI_TEST_MISSING"]["source"], "missing")

        saved = json.loads((self.root / "env.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["vars"]["TIANXI_TEST_TOKEN"]["value"], "super-secret")

        self.assertTrue(self.store.clear("TIANXI_TEST_TOKEN"))
        self.assertEqual(self.store.get("TIANXI_TEST_TOKEN"), "")
        self.assertFalse(self.store.clear("TIANXI_TEST_TOKEN"))

    def test_system_source_and_local_precedence(self) -> None:
        with mock.patch.dict(os.environ, {"TIANXI_TEST_SYSTEM": "from-system"}):
            self.assertEqual(self.store.source("TIANXI_TEST_SYSTEM"), "system")
            self.assertTrue(self.store.is_configured("TIANXI_TEST_SYSTEM"))

            self.store.set("TIANXI_TEST_SYSTEM", "from-local")
            self.store.activate()
            text, secrets = resolve_env("Bearer ${TIANXI_TEST_SYSTEM}")
            self.assertEqual(text, "Bearer from-local")
            self.assertEqual(secrets, ["from-local"])

    def test_invalid_name_and_oversized_value(self) -> None:
        with self.assertRaises(ResourceError):
            self.store.set("1BAD-NAME", "x")
        with self.assertRaises(ResourceError):
            self.store.set("TIANXI_TEST_BIG", "x" * 16001)
        with self.assertRaises(NetworkError):
            resolve_env("${TIANXI_TEST_NOT_SET_ANYWHERE}")


class CommandResolutionTests(unittest.TestCase):
    def test_merged_path_appends_registry_entries_once(self) -> None:
        root = temp_root()
        first, second, third = (str(root / name) for name in ("first", "second", "third"))
        with mock.patch.dict(
            os.environ, {"PATH": first + os.pathsep + second}, clear=False
        ), mock.patch.object(
            req_tools, "_registry_path", return_value=second + os.pathsep + third
        ):
            parts = req_tools.merged_path().split(os.pathsep)
        self.assertEqual(parts, [first, second, third])

    def test_missing_command_returns_empty(self) -> None:
        with mock.patch.object(req_tools, "_registry_path", return_value=""):
            self.assertEqual(req_tools.resolve_command("tianxi-no-such-command-xyz"), "")
            self.assertFalse(req_tools.command_available("tianxi-no-such-command-xyz"))

    def test_direct_path_is_used(self) -> None:
        root = temp_root()
        target = root / "tool.exe"
        target.write_bytes(b"")
        self.assertEqual(req_tools.resolve_command(str(target)), str(target))

    @unittest.skipUnless(os.name == "nt", "uvx sibling check is Windows-only")
    def test_uvx_without_sibling_uv_is_skipped(self) -> None:
        root = temp_root()
        broken = root / "broken"
        broken.mkdir()
        (broken / "uvx.exe").write_bytes(b"")
        good = root / "good"
        good.mkdir()
        (good / "uvx.exe").write_bytes(b"")
        (good / "uv.exe").write_bytes(b"")
        with mock.patch.dict(
            os.environ, {"PATH": str(broken) + os.pathsep + str(good)}, clear=False
        ), mock.patch.object(req_tools, "_registry_path", return_value=""):
            resolved = req_tools.resolve_command("uvx")
        self.assertEqual(Path(resolved).parent, good)
        self.assertTrue(req_tools.command_available("uvx"))


class RequirementInferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = temp_root()
        self.store = EnvStore(self.root / "env.json")
        self.addCleanup(set_env_provider, None)
        self.store.activate()

    def test_connector_env_names(self) -> None:
        http = {
            "type": "http",
            "base_url": "https://${API_HOST}/v1",
            "headers": {"Authorization": "Bearer ${API_TOKEN}"},
        }
        self.assertEqual(
            req_tools.connector_env_names(http), ["API_HOST", "API_TOKEN"]
        )
        mcp = {
            "type": "mcp",
            "command": "npx",
            "args": ["-y", "pkg", "${NOT_PARSED}"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "${GH_TOKEN}"},
        }
        self.assertEqual(req_tools.connector_env_names(mcp), ["GH_TOKEN"])

    def test_skill_frontmatter_and_sidecar(self) -> None:
        root = temp_root()
        skills = SkillStore(root / "skills", root / ".trash")
        skills.save(
            "req-skill",
            "依赖测试",
            "测试依赖声明",
            "# 步骤\n先读再写。\n",
            enabled=True,
            requires_env="GITHUB_PAT, NOTION_TOKEN",
            requires_commands=["npx"],
        )
        detail = skills.get("req-skill")
        self.assertEqual(detail["requires_env"], ["GITHUB_PAT", "NOTION_TOKEN"])
        self.assertEqual(detail["requires_commands"], ["npx"])

        sidecar = {
            "env": ["BRAVE_API_KEY"],
            "commands": ["uvx"],
            "downloads": [
                {"url": "https://example.com/a.txt", "path": "references/a.txt"}
            ],
        }
        (skills.folder_for("req-skill") / "requirements.json").write_text(
            json.dumps(sidecar), encoding="utf-8"
        )
        detail = skills.get("req-skill")
        status = req_tools.item_requirements("skill", detail, self.store)
        names = {item["name"] for item in status["requirements"]}
        self.assertEqual(
            names, {"GITHUB_PAT", "NOTION_TOKEN", "BRAVE_API_KEY", "npx", "uvx"}
        )
        self.assertEqual(len(status["downloads"]), 1)
        self.assertFalse(status["ready"])

    def test_old_seeded_skill_uses_hint(self) -> None:
        item = {
            "id": "github-rest-api",
            "name": "GitHub REST API",
            "enabled": True,
            "requires_env": [],
            "requires_commands": [],
            "requirements": {},
        }
        status = req_tools.item_requirements("skill", item, self.store)
        self.assertIn("GITHUB_PAT", {entry["name"] for entry in status["requirements"]})

    def test_prepare_plan_for_mcp(self) -> None:
        connector = {
            "id": "mcp-demo",
            "name": "MCP Demo",
            "type": "mcp",
            "command": "npx",
            "args": ["-y", "@scope/pkg"],
            "enabled": True,
            "env": {},
        }
        with mock.patch.object(
            req_tools, "command_available", side_effect=lambda name: name in {"npx", "npm"}
        ):
            plan = req_tools.prepare_plan("connector", connector, self.store)
        self.assertFalse(plan["blocked"])
        self.assertIn(
            ["npm", "cache", "add", "@scope/pkg"],
            [command["argv"] for command in plan["commands"]],
        )
        self.assertTrue(plan["has_actions"])

        with mock.patch.object(req_tools, "command_available", return_value=False):
            missing = req_tools.prepare_plan("connector", connector, self.store)
        self.assertTrue(missing["blocked"])
        self.assertTrue(missing["links"])
        self.assertFalse(missing["has_actions"])

    def test_prepare_plan_for_uvx_does_not_use_npm_cache(self) -> None:
        connector = {
            "id": "mcp-git",
            "name": "MCP Git",
            "type": "mcp",
            "command": "uvx",
            "args": ["mcp-server-git==2026.8.18", "--repository", "."],
            "enabled": True,
            "env": {},
        }
        with mock.patch.object(req_tools, "command_available", return_value=True):
            plan = req_tools.prepare_plan("connector", connector, self.store)
        argv = [command["argv"] for command in plan["commands"]]
        self.assertIn(["uv", "tool", "install", "mcp-server-git==2026.8.18"], argv)
        self.assertNotIn(["npm", "cache", "add", "mcp-server-git==2026.8.18"], argv)

    def test_prepare_plan_for_playwright_downloads_browser(self) -> None:
        connector = {
            "id": "mcp-playwright",
            "name": "Playwright",
            "type": "mcp",
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
            "enabled": True,
        }
        with mock.patch.object(req_tools, "command_available", return_value=True):
            plan = req_tools.prepare_plan("connector", connector, self.store)
        argv = [command["argv"] for command in plan["commands"]]
        self.assertIn(["npx", "-y", "playwright", "install", "chromium"], argv)

    def test_download_skill_resources(self) -> None:
        root = temp_root()
        skills = SkillStore(root / "skills", root / ".trash")
        skills.save("dl-skill", "下载测试", "测试下载", "# 内容\n正文\n")
        blob = Blob(url="https://example.com/a.txt", status=200, data=b"hello")
        with mock.patch.object(req_tools, "download_bytes", return_value=blob):
            results = req_tools.download_skill_resources(
                skills,
                "dl-skill",
                [{"url": "https://example.com/a.txt", "path": "references/a.txt"}],
            )
        self.assertTrue(results[0]["ok"])
        target = skills.folder_for("dl-skill") / "references" / "a.txt"
        self.assertEqual(target.read_bytes(), b"hello")

        with mock.patch.object(req_tools, "download_bytes") as fake:
            escaped = req_tools.download_skill_resources(
                skills,
                "dl-skill",
                [{"url": "https://example.com/b.txt", "path": "../escape.txt"}],
            )
        self.assertFalse(escaped[0]["ok"])
        fake.assert_not_called()
        self.assertFalse((root / "escape.txt").exists())

        oversized = Blob(
            url="https://example.com/big.bin",
            status=200,
            data=b"x" * (req_tools.MAX_DOWNLOAD_BYTES + 1),
        )
        with mock.patch.object(req_tools, "download_bytes", return_value=oversized):
            too_big = req_tools.download_skill_resources(
                skills,
                "dl-skill",
                [{"url": "https://example.com/big.bin", "path": "references/big.bin"}],
            )
        self.assertFalse(too_big[0]["ok"])


class RequirementToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_env_provider(None)

    def test_tools_registered(self) -> None:
        self.assertIn("check_requirements", TOOL_SPECS)
        self.assertIn("prepare_requirement", TOOL_SPECS)

    def test_check_requirements_reports_names_not_values(self) -> None:
        box, _root = make_box()
        box.run(
            "save_skill",
            {
                "skill_id": "need-env",
                "name": "需要令牌",
                "description": "测试环境变量检查",
                "body": "# 步骤\n读资料。\n",
                "requires_env": "TIANXI_REQ_TOKEN",
            },
        )
        output = box.run("check_requirements", {})
        self.assertIn("TIANXI_REQ_TOKEN", output)
        self.assertIn("缺 1 项", output)

        box.env_store.set("TIANXI_REQ_TOKEN", "do-not-leak-this")
        output = box.run("check_requirements", {"item": "need-env"})
        self.assertIn("就绪", output)
        self.assertNotIn("do-not-leak-this", output)

    def test_enabling_disabled_items_needs_approval(self) -> None:
        calls: list[str] = []
        allow = {"value": False}

        def approver(tool, *_args):
            calls.append(tool)
            return allow["value"]

        box, _root = make_box(
            settings={"approval_mode": "confirm_danger"}, approver=approver
        )
        box.run(
            "save_skill",
            {
                "skill_id": "closed-skill",
                "name": "关闭的技能",
                "description": "测试启用确认",
                "body": "# 内容\n正文\n",
                "enabled": False,
            },
        )
        refused = box.run("set_skill_enabled", {"skill_id": "closed-skill", "enabled": True})
        self.assertIn("主人拒绝", refused)
        self.assertFalse(box.skills.get("closed-skill")["enabled"])

        allow["value"] = True
        opened = box.run("set_skill_enabled", {"skill_id": "closed-skill", "enabled": True})
        self.assertIn("已启用", opened)
        self.assertTrue(box.skills.get("closed-skill")["enabled"])
        self.assertEqual(calls, ["enable_skill", "enable_skill"])

        box.connectors.save(
            {
                "id": "closed-connector",
                "name": "关闭的连接器",
                "type": "http",
                "base_url": "https://api.example.com",
                "enabled": False,
            }
        )
        allow["value"] = False
        refused = box.run(
            "set_connector_enabled", {"connector_id": "closed-connector", "enabled": True}
        )
        self.assertIn("主人拒绝", refused)
        self.assertFalse(box.connectors.get("closed-connector")["enabled"])

    def test_prepare_requirement_needs_approval(self) -> None:
        calls: list[str] = []

        def approver(tool, *_args):
            calls.append(tool)
            return False

        box, _root = make_box(
            settings={"approval_mode": "confirm_danger"}, approver=approver
        )
        box.connectors.save(
            {
                "id": "mcp-demo",
                "name": "MCP Demo",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "@scope/pkg"],
                "enabled": True,
            }
        )
        with mock.patch.object(req_tools, "command_available", return_value=True):
            with mock.patch.object(req_tools, "run_command") as runner:
                output = box.run(
                    "prepare_requirement", {"kind": "connector", "item_id": "mcp-demo"}
                )
        self.assertIn("主人拒绝", output)
        self.assertEqual(calls, ["prepare_requirement"])
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
