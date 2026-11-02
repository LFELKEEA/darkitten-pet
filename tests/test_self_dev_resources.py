from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.self_dev"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_self_dev import web as web_module  # noqa: E402
from lfelkeea_self_dev.catalog import (  # noqa: E402
    BUNDLED_CONNECTORS,
    BUNDLED_SKILLS,
    seed_catalog,
)
from lfelkeea_self_dev.connectors import ConnectorStore  # noqa: E402
from lfelkeea_self_dev.knowledge import KnowledgeBase  # noqa: E402
from lfelkeea_self_dev.net import NetworkError, Response  # noqa: E402
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


class SkillStoreTests(unittest.TestCase):
    def test_save_list_get_search_and_resources(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        saved = store.save(
            "release-check",
            "发布前检查",
            "准备发布插件之前使用",
            "# 步骤\n1. 跑测试\n2. 核对版本号\n",
            {"references/checklist.md": "- lint\n- test\n"},
        )
        self.assertEqual(saved["id"], "release-check")
        self.assertEqual(saved["name"], "发布前检查")
        self.assertEqual(
            sorted(saved["files"]), ["SKILL.md", "references/checklist.md"]
        )

        listed = store.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["description"], "准备发布插件之前使用")
        self.assertTrue(listed[0]["updated"])

        detail = store.get("release-check")
        self.assertIn("跑测试", detail["body"])
        resource = store.read_resource("release-check", "references/checklist.md")
        self.assertIn("- test", resource)

        hits = store.search("发布")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "release-check")
        self.assertIn("发布", hits[0]["snippet"])

    def test_validation_and_delete(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        with self.assertRaises(ResourceError):
            store.save("Bad Id", body="x")
        with self.assertRaises(ResourceError):
            store.save("okay-id", body="   ")
        store.save("okay-id", body="# 标题\n内容\n")
        with self.assertRaises(ResourceError):
            store.read_resource("okay-id", "../escape.md")
        message = store.delete("okay-id")
        self.assertTrue(message)
        self.assertFalse((root / "skills" / "okay-id").exists())
        with self.assertRaises(ResourceError):
            store.get("okay-id")

    def test_seed_defaults_only_once(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        self.assertEqual(store.seed_defaults(), 3)
        self.assertEqual(store.seed_defaults(), 0)
        ids = {item["id"] for item in store.list()}
        self.assertIn("pet-plugin-craft", ids)
        self.assertIn("verify-before-finish", ids)
        self.assertIn("research-before-coding", ids)
        store.delete("verify-before-finish")
        self.assertEqual(store.seed_defaults(), 0)

    def test_enabled_default_false_and_index_filter(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        saved = store.save("off-skill", "关闭技能", "默认关闭", "# 内容\n正文\n")
        self.assertFalse(saved["enabled"])
        self.assertNotIn("off-skill", store.index_text())
        self.assertEqual(store.search("正文", include_disabled=False), [])

        turned_on = store.set_enabled("off-skill", True)
        self.assertTrue(turned_on["enabled"])
        self.assertIn("off-skill", store.index_text())
        self.assertEqual(len(store.search("正文", include_disabled=False)), 1)

        turned_off = store.set_enabled("off-skill", False)
        self.assertFalse(turned_off["enabled"])
        self.assertEqual(store.search("正文", include_disabled=False), [])

    def test_legacy_skill_without_enabled_stays_on(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        folder = root / "skills" / "legacy"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            '---\nname: "旧技能"\ndescription: "没有 enabled 字段"\n---\n\n# 旧技能\n内容\n',
            encoding="utf-8",
        )
        self.assertTrue(store.get("legacy")["enabled"])
        self.assertIn("legacy", store.index_text())

    def test_seed_bundled_adds_missing_without_overwriting(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        store.save("keep-me", "用户版本", "不要覆盖", "# 用户\n内容\n", enabled=True)
        created = store.seed_bundled(
            [
                {"id": "keep-me", "name": "自带版本", "body": "# 自带\n内容\n", "enabled": False},
                {"id": "new-skill", "name": "新增", "body": "# 新增\n内容\n", "enabled": False},
            ]
        )
        self.assertEqual(created, 1)
        self.assertEqual(store.get("keep-me")["name"], "用户版本")
        self.assertTrue(store.get("keep-me")["enabled"])
        self.assertFalse(store.get("new-skill")["enabled"])


class KnowledgeBaseTests(unittest.TestCase):
    def test_add_search_citation_and_dedupe(self):
        kb = KnowledgeBase(temp_root() / "knowledge")
        text = "桌宠插件需要声明权限。插件权限包括 pet.say 和 timer。\n\n没有声明的权限会抛异常。"
        first = kb.add_document("插件权限", text, source="notes", kind="note")
        self.assertFalse(first["existing"])
        self.assertGreaterEqual(first["chunks"], 1)

        duplicate = kb.add_document("插件权限", text, source="notes", kind="note")
        self.assertTrue(duplicate["existing"])
        self.assertEqual(duplicate["id"], first["id"])

        hits = kb.search("权限")
        self.assertTrue(hits)
        self.assertTrue(hits[0]["citation"].startswith("[K"))
        self.assertIn("权限", hits[0]["snippet"])

        long_hits = kb.search("插件权限")
        self.assertTrue(long_hits)
        self.assertEqual(kb.stats()["documents"], 1)

    def test_long_document_chunking_and_preview(self):
        kb = KnowledgeBase(temp_root() / "knowledge")
        paragraph = "这是一段用于测试分块的长文本。" * 160
        result = kb.add_document("长文", "\n\n".join([paragraph] * 4), source="long")
        self.assertGreater(result["chunks"], 2)
        document = kb.get_document(result["id"])
        self.assertEqual(document["title"], "长文")
        self.assertGreater(len(document["text"]), 1000)
        self.assertIn("长文", [item["title"] for item in kb.list_documents()])

    def test_import_file_and_archive_delete(self):
        root = temp_root()
        kb = KnowledgeBase(root / "knowledge")
        source = root / "note.md"
        source.write_text("# 记录\n数据库用 SQLite FTS5。\n", encoding="utf-8")
        result = kb.import_file(source)
        self.assertEqual(result["title"], "note")
        self.assertEqual(kb.get_document(result["id"])["kind"], "file")

        binary = root / "image.bin"
        binary.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
        with self.assertRaises(ResourceError):
            kb.import_file(binary)

        title, backup = kb.archive_and_delete(result["id"], root / ".trash")
        self.assertEqual(title, "note")
        self.assertTrue(backup.is_file())
        self.assertIn("SQLite FTS5", backup.read_text(encoding="utf-8"))
        with self.assertRaises(ResourceError):
            kb.get_document(result["id"])


class _FakeResponse:
    def __init__(self, url: str, body: bytes, *, status: int = 200, content_type: str = "application/json"):
        self._url = url
        self._body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False


class _FakeOpener:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.requests: list[object] = []

    def open(self, request, timeout=None):  # noqa: ARG002
        self.requests.append(request)
        return self.response


class ConnectorTests(unittest.TestCase):
    def _store(self) -> tuple[ConnectorStore, Path]:
        root = temp_root()
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        return ConnectorStore(root / "connectors.json", workspace, root / ".trash"), root

    def test_save_public_masks_plain_secrets(self):
        store, _root = self._store()
        item = store.save(
            {
                "id": "demo.api",
                "type": "http",
                "base_url": "https://api.example.com/v1/",
                "headers": {"Authorization": "Bearer plain-token", "X-Env": "${DEMO_TOKEN}"},
            }
        )
        self.assertEqual(item["base_url"], "https://api.example.com/v1")
        public = ConnectorStore.public(item)
        self.assertEqual(public["headers"]["Authorization"], "***")
        self.assertEqual(public["headers"]["X-Env"], "${DEMO_TOKEN}")
        self.assertIn("demo.api", store.index_text())
        self.assertNotIn("plain-token", json.dumps(public, ensure_ascii=False))

    def test_legacy_double_brace_env_refs_are_migrated(self):
        store, _root = self._store()
        item = store.save(
            {
                "id": "legacy.api",
                "type": "http",
                "base_url": "{{DEMO_BASE_URL}}",
                "headers": {"Authorization": "Bearer {{DEMO_TOKEN}}"},
            }
        )
        self.assertEqual(item["base_url"], "${DEMO_BASE_URL}")
        self.assertEqual(item["headers"]["Authorization"], "Bearer ${DEMO_TOKEN}")
        self.assertEqual(
            ConnectorStore.public(store.get("legacy.api"))["headers"]["Authorization"],
            "Bearer ${DEMO_TOKEN}",
        )
        store.save(
            {
                "id": "legacy.mcp",
                "type": "mcp",
                "command": "npx",
                "args": ["-y", "demo-mcp"],
                "env": {"DEMO_TOKEN": "{{DEMO_TOKEN}}"},
            }
        )
        self.assertEqual(store.get("legacy.mcp")["env"]["DEMO_TOKEN"], "${DEMO_TOKEN}")

    def test_http_call_expands_env_and_redacts_response(self):
        store, _root = self._store()
        store.save(
            {
                "id": "demo.api",
                "type": "http",
                "base_url": "https://api.example.com/v1",
                "headers": {"Authorization": "Bearer ${SELFDEV_TEST_TOKEN}"},
            }
        )
        secret = "selfdev-super-secret"
        body = json.dumps({"token": secret, "ok": True}).encode("utf-8")
        opener = _FakeOpener(_FakeResponse("https://api.example.com/v1/status", body))
        with mock.patch.dict(os.environ, {"SELFDEV_TEST_TOKEN": secret}, clear=False):
            with mock.patch("urllib.request.build_opener", return_value=opener):
                result = store.http_call("demo.api", path="status", query={"q": "中文"})
        self.assertEqual(result["status"], 200)
        self.assertIn("https://api.example.com/v1/status", result["url"])
        self.assertIn("q=%E4%B8%AD%E6%96%87", result["url"])
        self.assertNotIn(secret, result["text"])
        self.assertIn("***", result["text"])
        request = opener.requests[0]
        self.assertEqual(request.get_header("Authorization"), f"Bearer {secret}")

    def test_http_call_rejects_private_and_absolute_path(self):
        store, _root = self._store()
        store.save(
            {
                "id": "local.api",
                "type": "http",
                "base_url": "http://127.0.0.1:9",
            }
        )
        with self.assertRaises(NetworkError):
            store.http_call("local.api")
        store.save(
            {
                "id": "public.api",
                "type": "http",
                "base_url": "https://api.example.com",
            }
        )
        with self.assertRaises(ResourceError):
            store.http_call("public.api", path="http://evil.example.com/x")

    def test_delete_keeps_backup(self):
        store, _root = self._store()
        store.save({"id": "demo.api", "type": "http", "base_url": "https://api.example.com"})
        message = store.delete("demo.api")
        self.assertIn("demo.api", message)
        self.assertEqual(store.list(), [])
        self.assertTrue(list(store.path.parent.glob("connectors.json.*.bak")))

    def test_enabled_toggle_and_index_filter(self):
        store, _root = self._store()
        store.save({"id": "demo.api", "type": "http", "base_url": "https://api.example.com"})
        self.assertIn("demo.api", store.index_text())

        off = store.set_enabled("demo.api", False)
        self.assertFalse(off["enabled"])
        self.assertNotIn("demo.api", store.index_text())
        self.assertIn("关闭", store.index_text())

        on = store.set_enabled("demo.api", True)
        self.assertTrue(on["enabled"])
        self.assertIn("demo.api", store.index_text())

    def test_base_url_can_be_env_placeholder(self):
        store, _root = self._store()
        store.save({"id": "env.api", "type": "http", "base_url": "${SELFDEV_BASE_URL}"})
        body = json.dumps({"ok": True}).encode("utf-8")
        opener = _FakeOpener(_FakeResponse("https://api.example.com/v1", body))
        with mock.patch.dict(
            os.environ, {"SELFDEV_BASE_URL": "https://api.example.com/v1"}, clear=False
        ):
            with mock.patch("urllib.request.build_opener", return_value=opener):
                result = store.http_call("env.api")
        self.assertEqual(result["url"], "https://api.example.com/v1")

    def test_seed_bundled_connectors_adds_missing_only(self):
        store, _root = self._store()
        store.save(
            {
                "id": "keep.api",
                "type": "http",
                "base_url": "https://api.example.com",
                "enabled": True,
            }
        )
        created = store.seed_bundled(
            [
                {
                    "id": "keep.api",
                    "type": "http",
                    "base_url": "https://other.example.com",
                    "enabled": False,
                },
                {
                    "id": "new.api",
                    "type": "http",
                    "base_url": "https://new.example.com",
                    "enabled": False,
                },
            ]
        )
        self.assertEqual(created, 1)
        self.assertEqual(store.get("keep.api")["base_url"], "https://api.example.com")
        self.assertTrue(store.get("keep.api")["enabled"])
        self.assertFalse(store.get("new.api")["enabled"])

    def test_mcp_stdio_client(self):
        store, root = self._store()
        script = root / "fake_mcp.py"
        script.write_text(
            """
import json
import sys

def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\\n")
    sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    message = json.loads(line)
    if "id" not in message:
        continue
    method = message.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "0.1"},
        }
    elif method == "tools/list":
        result = {
            "tools": [
                {
                    "name": "echo",
                    "description": "回显文本",
                    "inputSchema": {"type": "object"},
                }
            ]
        }
    elif method == "tools/call":
        arguments = message.get("params", {}).get("arguments", {})
        result = {
            "content": [{"type": "text", "text": "echo: " + str(arguments.get("text", ""))}]
        }
    else:
        result = {}
    send({"jsonrpc": "2.0", "id": message["id"], "result": result})
""".strip()
            + "\n",
            encoding="utf-8",
        )
        store.save(
            {
                "id": "fake.mcp",
                "type": "mcp",
                "command": sys.executable,
                "args": [str(script)],
                "timeout": 20,
            }
        )
        tools = store.mcp_tools("fake.mcp")
        self.assertEqual(tools["server"]["name"], "fake")
        self.assertEqual([item["name"] for item in tools["tools"]], ["echo"])

        called = store.mcp_call("fake.mcp", "echo", {"text": "你好"})
        self.assertIn("echo: 你好", called["text"])


class WebTests(unittest.TestCase):
    def test_web_search_parses_duckduckgo_html(self):
        html = """
        <div class="result">
          <a class="result__a" href="https://example.com/a">示例标题</a>
          <a class="result__snippet">这是摘要</a>
        </div>
        """
        response = Response(
            url="https://html.duckduckgo.com/html/",
            status=200,
            content_type="text/html",
            text=html,
        )
        with mock.patch.object(web_module, "http_request", return_value=response):
            text = web_module.web_search("测试")
        self.assertIn("示例标题", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn("这是摘要", text)

    def test_web_search_falls_back_to_bing_rss(self):
        empty = Response(
            url="https://html.duckduckgo.com/html/",
            status=200,
            content_type="text/html",
            text="<html><body>没有结果</body></html>",
        )
        rss = Response(
            url="https://www.bing.com/search",
            status=200,
            content_type="application/rss+xml",
            text="""
            <rss><channel>
              <item>
                <title><![CDATA[官方文档]]></title>
                <link>https://example.com/docs</link>
                <description>&lt;p&gt;安装与&lt;b&gt;配置&lt;/b&gt;说明&lt;/p&gt;</description>
              </item>
            </channel></rss>
            """,
        )
        with mock.patch.object(
            web_module, "http_request", side_effect=[empty, rss]
        ):
            text = web_module.web_search("文档")
        self.assertIn("Bing", text)
        self.assertIn("官方文档", text)
        self.assertIn("https://example.com/docs", text)
        self.assertIn("安装与", text)
        self.assertIn("配置", text)
        self.assertIn("说明", text)

    def test_plugin_loads_without_sqlite_or_xml(self):
        """打包版桌宠没有 sqlite3 / xml.etree，插件必须照常加载。"""
        code = f"""
import builtins
import sys

real_import = builtins.__import__

def guard(name, *args, **kwargs):
    if name == "sqlite3" or name == "xml" or name.startswith("xml."):
        raise ImportError("blocked " + name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guard
sys.path.insert(0, {str(PLUGIN)!r})

from lfelkeea_self_dev.service import DevService  # noqa: F401
from lfelkeea_self_dev.knowledge import KnowledgeBase
from lfelkeea_self_dev import web

import tempfile
from pathlib import Path

kb = KnowledgeBase(Path(tempfile.mkdtemp()) / "knowledge")
kb.add_document("标题", "正文里有连接器和知识库")
hits = kb.search("知识库")
assert hits and hits[0]["citation"].startswith("[K"), hits
assert "标题" in web.strip_html("<h1>标题</h1>")
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)

    def test_search_github_parses_json(self):
        payload = {
            "items": [
                {
                    "full_name": "owner/repo",
                    "html_url": "https://github.com/owner/repo",
                    "description": "一个仓库",
                    "stargazers_count": 1234,
                    "language": "Python",
                    "updated_at": "2026-01-02T03:04:05Z",
                }
            ]
        }
        response = Response(
            url="https://api.github.com/search/repositories",
            status=200,
            content_type="application/json",
            text=json.dumps(payload),
        )
        with mock.patch.object(web_module, "http_request", return_value=response):
            text = web_module.search_github("agent")
        self.assertIn("owner/repo", text)
        self.assertIn("★1234", text)
        self.assertIn("Python", text)

    def test_pypi_info_and_fetch_readable(self):
        payload = {
            "info": {
                "name": "demo",
                "version": "1.2.3",
                "summary": "演示包",
                "home_page": "https://example.com/demo",
            },
            "releases": {"1.2.3": [{"upload_time_iso_8601": "2026-01-02T00:00:00Z"}]},
        }
        json_response = Response(
            url="https://pypi.org/pypi/demo/json",
            status=200,
            content_type="application/json",
            text=json.dumps(payload),
        )
        with mock.patch.object(web_module, "http_request", return_value=json_response):
            info = web_module.pypi_info("demo")
        self.assertIn("1.2.3", info)
        self.assertIn("演示包", info)

        page = Response(
            url="https://example.com/page",
            status=200,
            content_type="text/html",
            text="<html><head><title>标题</title></head><body><p>正文内容</p></body></html>",
        )
        with mock.patch.object(web_module, "http_request", return_value=page):
            title, body = web_module.fetch_readable("https://example.com/page")
        self.assertEqual(title, "标题")
        self.assertIn("正文内容", body)


class ToolBoxResourceTests(unittest.TestCase):
    def test_new_tools_are_registered(self):
        for name in (
            "list_skills",
            "read_skill",
            "search_skills",
            "save_skill",
            "delete_skill",
            "set_skill_enabled",
            "list_connectors",
            "call_connector",
            "mcp_tools",
            "call_mcp_tool",
            "save_connector",
            "delete_connector",
            "set_connector_enabled",
            "search_knowledge",
            "import_knowledge",
            "import_knowledge_file",
            "import_knowledge_url",
            "list_knowledge",
            "delete_knowledge",
            "web_search",
            "search_github",
            "pypi_info",
            "search_pypi",
        ):
            self.assertIn(name, TOOL_SPECS)

    def test_disabled_skill_and_connector_are_refused(self):
        box, _root = make_box()
        box.run(
            "save_skill",
            {
                "skill_id": "switchable",
                "name": "可开关技能",
                "description": "测试开关",
                "body": "# 内容\n测试正文\n",
            },
        )
        self.assertIn("[开]", box.run("list_skills", {}))
        self.assertIn("已关闭", box.run("set_skill_enabled", {"skill_id": "switchable", "enabled": False}))
        refused = box.run("read_skill", {"skill_id": "switchable"})
        self.assertIn("处于关闭状态", refused)
        self.assertNotIn("switchable", box.run("search_skills", {"query": "开关"}))
        self.assertIn("[关]", box.run("list_skills", {}))

        box.run(
            "save_connector",
            {
                "id": "switch.api",
                "type": "http",
                "base_url": "https://api.example.com",
            },
        )
        self.assertIn("[开]", box.run("list_connectors", {}))
        self.assertIn(
            "已关闭",
            box.run("set_connector_enabled", {"connector_id": "switch.api", "enabled": False}),
        )
        refused_call = box.run("call_connector", {"connector_id": "switch.api"})
        self.assertIn("处于关闭状态", refused_call)
        self.assertIn("[关]", box.run("list_connectors", {}))

    def test_skill_and_knowledge_tools(self):
        box, _root = make_box()
        saved = box.run(
            "save_skill",
            {
                "skill_id": "tool-test",
                "name": "工具测试",
                "description": "测试技能工具",
                "body": "# 步骤\n先读文件，再改代码。\n",
            },
        )
        self.assertIn("已保存技能", saved)
        self.assertIn("tool-test", box.run("list_skills", {}))
        self.assertIn("先读文件", box.run("read_skill", {"skill_id": "tool-test"}))
        self.assertIn("工具测试", box.run("search_skills", {"query": "工具"}))

        imported = box.run(
            "import_knowledge",
            {"title": "测试资料", "text": "自开发工坊支持技能、连接器和知识库。", "source": "test"},
        )
        self.assertIn("已写入知识库文档", imported)
        hits = box.run("search_knowledge", {"query": "连接器"})
        self.assertIn("测试资料", hits)
        self.assertIn("[K", hits)
        self.assertIn("测试资料", box.run("list_knowledge", {}))

    def test_connector_tools_and_network_switch(self):
        box, _root = make_box()
        result = box.run(
            "save_connector",
            {
                "id": "demo.api",
                "type": "http",
                "base_url": "https://api.example.com",
                "headers": {"Authorization": "Bearer ${DEMO_TOKEN}"},
            },
        )
        self.assertIn("已保存连接器", result)
        listing = box.run("list_connectors", {})
        self.assertIn("demo.api", listing)
        self.assertNotIn("Bearer plain-token", listing)

        offline, _root2 = make_box(settings={"approval_mode": "auto", "allow_network": False})
        blocked = offline.run("web_search", {"query": "test"})
        self.assertIn("禁止联网", blocked)

    def test_always_confirm_deletes(self):
        calls: list[str] = []

        def approver(tool, *_args):
            calls.append(tool)
            return False

        box, _root = make_box(approver=approver)
        box.run("save_skill", {"skill_id": "to-delete", "body": "# x\n内容\n"})
        result = box.run("delete_skill", {"skill_id": "to-delete"})
        self.assertIn("主人拒绝", result)
        self.assertIn("to-delete", box.run("list_skills", {}))
        self.assertEqual(calls, ["delete_skill"])


class BundledCatalogTests(unittest.TestCase):
    def test_catalog_entries_are_unique_and_default_off(self):
        skill_ids = [item["id"] for item in BUNDLED_SKILLS]
        connector_ids = [item["id"] for item in BUNDLED_CONNECTORS]
        self.assertEqual(len(skill_ids), len(set(skill_ids)))
        self.assertEqual(len(connector_ids), len(set(connector_ids)))
        self.assertGreaterEqual(len(skill_ids), 20)
        self.assertGreaterEqual(len(connector_ids), 20)
        self.assertTrue(all(item["enabled"] is False for item in BUNDLED_SKILLS))
        self.assertTrue(all(item["enabled"] is False for item in BUNDLED_CONNECTORS))
        self.assertIn("github", connector_ids)
        self.assertIn("github-public", connector_ids)
        self.assertIn("feishu", connector_ids)
        self.assertIn("notion", connector_ids)
        self.assertIn("searxng", connector_ids)
        self.assertIn("sql-gateway", connector_ids)
        self.assertIn("mcp-blender", connector_ids)
        self.assertIn("openai-api", connector_ids)
        self.assertIn("tavily", connector_ids)
        for skill_id in (
            "docx-documents",
            "pdf-documents",
            "xlsx-spreadsheets",
            "pptx-presentations",
            "knowledge-base",
            "drawio-diagrams",
            "technical-diagrams",
            "image-generation",
            "web-research",
            "skill-authoring",
        ):
            self.assertIn(skill_id, skill_ids)

    def test_catalog_connectors_normalize(self):
        root = temp_root()
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        store = ConnectorStore(root / "connectors.json", workspace, root / ".trash")
        self.assertEqual(store.seed_bundled(BUNDLED_CONNECTORS), len(BUNDLED_CONNECTORS))
        self.assertEqual(store.seed_bundled(BUNDLED_CONNECTORS), 0)
        items = store.list()
        self.assertEqual(len(items), len(BUNDLED_CONNECTORS))
        self.assertTrue(all(not item["enabled"] for item in items))

    def test_catalog_skills_are_written(self):
        root = temp_root()
        store = SkillStore(root / "skills", root / ".trash")
        self.assertEqual(store.seed_bundled(BUNDLED_SKILLS), len(BUNDLED_SKILLS))
        self.assertEqual(store.seed_bundled(BUNDLED_SKILLS), 0)
        items = store.list()
        self.assertEqual(len(items), len(BUNDLED_SKILLS))
        self.assertTrue(all(not item["enabled"] for item in items))
        self.assertIn("关", store.index_text())

    def test_bundled_mcp_git_uses_working_npx_launcher(self):
        git = next(item for item in BUNDLED_CONNECTORS if item["id"] == "mcp-git")
        self.assertEqual(git["command"], "npx")
        self.assertIn("@selfagency/git-mcp", git["args"])
        self.assertIn("--repo-path", git["args"])
        self.assertNotEqual(git["command"], "uvx")

    def test_legacy_uvx_mcp_git_is_migrated(self):
        root = temp_root()
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        connectors = ConnectorStore(
            root / "connectors.json", workspace, root / ".trash"
        )
        connectors.save(
            {
                "id": "mcp-git",
                "name": "MCP Git",
                "type": "mcp",
                "description": "旧配置",
                "enabled": True,
                "command": "uvx",
                "args": ["mcp-server-git", "--repository", "."],
                "cwd": ".",
                "timeout": 60,
            }
        )

        seed_catalog(
            SimpleNamespace(
                skills=root / "skills",
                connectors=root / "connectors.json",
                workspace=workspace,
                trash=root / ".trash",
            )
        )

        git = connectors.get("mcp-git")
        self.assertEqual(git["command"], "npx")
        self.assertIn("@selfagency/git-mcp", git["args"])
        self.assertEqual(git["timeout"], 120)
        self.assertTrue(git["enabled"])

    def test_legacy_github_entries_are_renamed_without_losing_settings(self):
        root = temp_root()
        workspace = root / "workspace"
        workspace.mkdir(parents=True)
        skills = SkillStore(root / "skills", root / ".trash")
        connectors = ConnectorStore(
            root / "connectors.json", workspace, root / ".trash"
        )
        connectors.save(
            {
                "id": "github",
                "name": "GitHub REST API",
                "type": "http",
                "description": "旧描述",
                "enabled": True,
                "base_url": "https://api.github.com",
                "headers": {"Authorization": "Bearer ${GITHUB_PAT}"},
                "timeout": 45,
            }
        )
        skills.save(
            "github-rest-api",
            "GitHub REST API",
            "旧描述",
            "# 旧正文\n保留这段内容。\n",
            enabled=True,
        )

        seed_catalog(
            SimpleNamespace(
                skills=root / "skills",
                connectors=root / "connectors.json",
                workspace=workspace,
                trash=root / ".trash",
            )
        )

        own = connectors.get("github")
        self.assertEqual(own["name"], "GitHub 自己的仓库（令牌）")
        self.assertTrue(own["enabled"])
        self.assertEqual(
            own["headers"]["Authorization"], "Bearer ${GITHUB_PAT}"
        )
        public = connectors.get("github-public")
        self.assertNotIn("Authorization", public["headers"])

        skill = skills.get("github-rest-api")
        self.assertEqual(skill["name"], "GitHub 自己的仓库（令牌）")
        self.assertTrue(skill["enabled"])
        self.assertEqual(skill["requires_env"], ["GITHUB_PAT"])
        self.assertIn("保留这段内容", skill["body"])
        self.assertTrue((root / "skills" / "github-public-api" / "SKILL.md").is_file())


class SettingsTests(unittest.TestCase):
    def test_new_resource_settings_defaults(self):
        settings = normalize_settings({})
        self.assertTrue(settings["allow_connectors"])
        self.assertTrue(settings["auto_learn"])
        custom = normalize_settings({"allow_connectors": False, "auto_learn": False})
        self.assertFalse(custom["allow_connectors"])
        self.assertFalse(custom["auto_learn"])


if __name__ == "__main__":
    unittest.main()
