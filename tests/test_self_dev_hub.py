from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.self_dev"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_self_dev import hub  # noqa: E402
from lfelkeea_self_dev.net import Response  # noqa: E402
from lfelkeea_self_dev.settings import normalize_settings  # noqa: E402
from lfelkeea_self_dev.tools import TOOL_SPECS, ToolBox  # noqa: E402


def make_box(settings=None, approver=None):
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
        approver=approver,
        stop_event=threading.Event(),
    )
    return box, root


def json_response(payload, url="https://api.github.com/x") -> Response:
    return Response(
        url=url,
        status=200,
        content_type="application/json",
        text=json.dumps(payload),
    )


class HubTests(unittest.TestCase):
    def test_search_repos_parses_github(self):
        payload = {
            "items": [
                {
                    "full_name": "owner/skills",
                    "html_url": "https://github.com/owner/skills",
                    "description": "一包技能",
                    "stargazers_count": 321,
                    "updated_at": "2026-01-02T03:04:05Z",
                    "language": "Python",
                }
            ]
        }
        with mock.patch.object(hub, "http_request", return_value=json_response(payload)) as request:
            items = hub.search_skill_repos("agent", limit=5)
        self.assertEqual(items[0]["repo"], "owner/skills")
        self.assertEqual(items[0]["stars"], 321)
        self.assertIn("skills", request.call_args.args[0])

    def test_list_repo_skills_finds_manifests(self):
        def fake(path, params=None):
            if path == "/repos/owner/skills":
                return {"default_branch": "main"}
            if path.endswith("/git/trees/main"):
                return {
                    "tree": [
                        {"path": "SKILL.md", "type": "blob", "size": 100},
                        {"path": "extra/SKILL.md", "type": "blob", "size": 100},
                        {"path": "README.md", "type": "blob", "size": 100},
                    ]
                }
            raise AssertionError(path)

        with mock.patch.object(hub, "_github_json", side_effect=fake):
            found, branch = hub.list_repo_skills("owner/skills")
        self.assertEqual(branch, "main")
        self.assertEqual([item["path"] for item in found], ["SKILL.md", "extra/SKILL.md"])

    def test_fetch_skill_reads_manifest_and_resources(self):
        tree = [
            {"path": "demo/SKILL.md", "type": "blob", "size": 100},
            {"path": "demo/references/notes.md", "type": "blob", "size": 20},
            {"path": "demo/assets/logo.png", "type": "blob", "size": 300},
        ]
        manifest = '---\nname: "演示技能"\ndescription: "演示用"\n---\n\n# 步骤\n\n先读文件。\n'
        files = {
            "demo/SKILL.md": manifest,
            "demo/references/notes.md": "补充说明",
        }

        def read(repo, ref, path, limit=0):
            return files[path]

        with mock.patch.object(hub, "_tree", return_value=(tree, "main")), \
                mock.patch.object(hub, "_raw_text", side_effect=read):
            data = hub.fetch_skill("owner/skills", "demo/SKILL.md")
        self.assertEqual(data["name"], "演示技能")
        self.assertEqual(data["description"], "演示用")
        self.assertIn("先读文件", data["body"])
        self.assertEqual(data["resources"], {"references/notes.md": "补充说明"})
        self.assertTrue(any("logo.png" in item for item in data["skipped"]))

    def test_fetch_skill_rejects_oversized_body(self):
        tree = [{"path": "demo/SKILL.md", "type": "blob", "size": 100}]
        manifest = "---\nname: big\n---\n\n" + ("步骤\n" * (hub.MAX_SKILL_CHARS // 2))
        with mock.patch.object(hub, "_tree", return_value=(tree, "main")), \
                mock.patch.object(hub, "_raw_text", return_value=manifest):
            with self.assertRaises(hub.NetworkError) as caught:
                hub.fetch_skill("owner/skills", "demo/SKILL.md")
        self.assertIn("超过本地", str(caught.exception))

    def test_root_skill_does_not_swallow_whole_repo(self):
        tree = [
            {"path": "SKILL.md", "type": "blob", "size": 100},
            {"path": "references/a.md", "type": "blob", "size": 20},
            {"path": "docs/readme.md", "type": "blob", "size": 20},
            {"path": "other/SKILL.md", "type": "blob", "size": 20},
        ]
        manifest = '---\nname: "根技能"\ndescription: "演示"\n---\n\n# 步骤\n\n先读文件。\n'
        files = {"SKILL.md": manifest, "references/a.md": "补充"}

        def read(repo, ref, path, limit=0):
            return files[path]

        with mock.patch.object(hub, "_tree", return_value=(tree, "main")), \
                mock.patch.object(hub, "_raw_text", side_effect=read):
            data = hub.fetch_skill("owner/skills", "SKILL.md")
        self.assertEqual(data["resources"], {"references/a.md": "补充"})

    def test_resolve_mcp_command_prefers_explicit_then_npm_then_pypi(self):
        explicit = hub.resolve_mcp_command("whatever", "node", ["server.js"])
        self.assertEqual(explicit["command"], "node")
        self.assertEqual(explicit["source"], "custom")

        with mock.patch.object(hub, "_npm_exists", return_value=True):
            npm = hub.resolve_mcp_command("@scope/server")
        self.assertEqual(npm["command"], "npx")
        self.assertEqual(npm["args"][:2], ["-y", "@scope/server"])

        with mock.patch.object(hub, "_npm_exists", return_value=False), \
                mock.patch.object(hub, "_pypi_exists", return_value=True):
            pypi = hub.resolve_mcp_command("demo-mcp")
        self.assertEqual(pypi["command"], "uvx")
        self.assertEqual(pypi["args"], ["demo-mcp"])


class HubToolTests(unittest.TestCase):
    def test_new_tools_are_registered(self):
        for name in (
            "search_skills_online",
            "list_repo_skills",
            "install_skill",
            "read_repo_file",
            "search_mcp_servers",
            "install_mcp_server",
        ):
            self.assertIn(name, TOOL_SPECS)

    def test_search_skills_online_formats_results(self):
        box, _root = make_box()
        items = [
            {
                "repo": "owner/skills",
                "url": "https://github.com/owner/skills",
                "description": "技能合集",
                "stars": 10,
                "updated": "2026-01-01",
                "language": "Python",
            }
        ]
        with mock.patch.object(hub, "search_skill_repos", return_value=items):
            text = box.run("search_skills_online", {"query": "plugin"})
        self.assertIn("owner/skills", text)
        self.assertIn("list_repo_skills", text)

    def test_install_skill_saves_and_avoids_id_collision(self):
        box, _root = make_box()
        payload = {
            "repo": "owner/skills",
            "ref": "main",
            "path": "demo/SKILL.md",
            "folder": "demo",
            "name": "演示",
            "description": "演示技能",
            "body": "# 步骤\n\n先读文件。\n",
            "resources": {"references/notes.md": "补充"},
            "skipped": [],
        }
        box.skills.save("demo", "已有", "已占用", "# 已有")
        with mock.patch.object(hub, "fetch_skill", return_value=payload):
            first = box.run("install_skill", {"repo": "owner/skills", "path": "demo/SKILL.md"})
        self.assertIn("demo-2", first)
        self.assertTrue((box.skills.root / "demo-2" / "SKILL.md").is_file())
        with mock.patch.object(hub, "fetch_skill", return_value=payload):
            second = box.run("install_skill", {"repo": "owner/skills", "path": "demo/SKILL.md"})
        self.assertIn("demo-3", second)

    def test_install_skill_respects_rejection(self):
        box, _root = make_box(
            settings={"approval_mode": "confirm_danger"},
            approver=lambda *args: False,
        )
        payload = {
            "repo": "owner/skills",
            "ref": "main",
            "path": "demo/SKILL.md",
            "folder": "demo",
            "name": "演示",
            "description": "",
            "body": "# 步骤\n",
            "resources": {},
            "skipped": [],
        }
        with mock.patch.object(hub, "fetch_skill", return_value=payload):
            text = box.run("install_skill", {"repo": "owner/skills", "path": "demo/SKILL.md"})
        self.assertIn("拒绝", text)
        self.assertFalse((box.skills.root / "demo").exists())

    def test_install_mcp_server_saves_connector(self):
        box, _root = make_box()
        resolved = {"command": "npx", "args": ["-y", "demo-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=resolved), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            text = box.run("install_mcp_server", {"package": "@scope/demo-mcp"})
        self.assertIn("已保存 MCP 连接器", text)
        saved = box.connectors.get("demo-mcp")
        self.assertEqual(saved["command"], "npx")
        self.assertEqual(saved["args"], ["-y", "demo-mcp"])

    def test_install_mcp_server_does_not_overwrite_existing_connector(self):
        box, _root = make_box()
        first = {"command": "npx", "args": ["-y", "@a/demo-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=first), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            box.run("install_mcp_server", {"package": "@a/demo-mcp"})
        second = {"command": "npx", "args": ["-y", "@b/demo-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=second), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            text = box.run("install_mcp_server", {"package": "@b/demo-mcp"})
        self.assertIn("demo-mcp-2", text)
        self.assertEqual(box.connectors.get("demo-mcp")["args"], ["-y", "@a/demo-mcp"])
        self.assertEqual(box.connectors.get("demo-mcp-2")["args"], ["-y", "@b/demo-mcp"])

    def test_install_mcp_server_rejects_explicit_taken_id(self):
        box, _root = make_box()
        first = {"command": "npx", "args": ["-y", "old-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=first), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            box.run("install_mcp_server", {"package": "old-mcp"})
        second = {"command": "npx", "args": ["-y", "new-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=second), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            text = box.run(
                "install_mcp_server",
                {"package": "new-mcp", "connector_id": "old-mcp"},
            )
        self.assertIn("已被占用", text)
        self.assertEqual(box.connectors.get("old-mcp")["args"], ["-y", "old-mcp"])

    def test_install_mcp_server_same_command_is_idempotent(self):
        box, _root = make_box()
        resolved = {"command": "npx", "args": ["-y", "demo-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=resolved), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            box.run("install_mcp_server", {"package": "demo-mcp"})
            text = box.run("install_mcp_server", {"package": "demo-mcp"})
        self.assertIn("无需重复安装", text)
        self.assertEqual(len(box.connectors.list()), 1)

    def test_install_mcp_server_rejects_missing_runtime(self):
        box, _root = make_box()
        resolved = {"command": "uvx", "args": ["demo-mcp"], "source": "pypi"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=resolved), \
                mock.patch.object(hub, "runtime_available", return_value=False):
            text = box.run("install_mcp_server", {"package": "demo-mcp"})
        self.assertIn("找不到启动命令", text)
        self.assertEqual(box.connectors.list(), [])

    def test_install_mcp_server_respects_rejection(self):
        box, _root = make_box(
            settings={"approval_mode": "confirm_danger"},
            approver=lambda *args: False,
        )
        resolved = {"command": "npx", "args": ["-y", "demo-mcp"], "source": "npm"}
        with mock.patch.object(hub, "resolve_mcp_command", return_value=resolved), \
                mock.patch.object(hub, "runtime_available", return_value=True):
            text = box.run("install_mcp_server", {"package": "demo-mcp"})
        self.assertIn("拒绝", text)
        self.assertEqual(box.connectors.list(), [])


if __name__ == "__main__":
    unittest.main()
