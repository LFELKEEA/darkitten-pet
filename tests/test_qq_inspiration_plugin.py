from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_inspiration"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_inspiration import extractor, inspiration_store, memory_reader  # noqa: E402


class FakeConversation:
    def __init__(self, rows):
        self.rows = rows

    def messages(self, limit=None):
        return list(self.rows[-limit:] if limit else self.rows)


class FakeAPI:
    def __init__(self, storage=None, settings=None, conversation=None):
        self.storage = storage if isinstance(storage, dict) else {}
        self.settings = settings if isinstance(settings, dict) else {}
        self.manager = SimpleNamespace(conversation=conversation)
        self.saved = 0
        self.said: list[str] = []
        self.logs: list[str] = []
        self.handlers: dict[str, list] = {}

    def save_storage(self):
        self.saved += 1

    def say(self, text, duration=5000):
        self.said.append(text)

    def log(self, message):
        self.logs.append(message)

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)


class ExtractorTests(unittest.TestCase):
    def test_parse_fenced_array(self):
        reply = '```json\n[{"title":"喝水提醒","idea":"每隔一小时让桌宠提醒喝水","tags":["健康"]}]\n```'
        items = extractor.parse_ideas(reply)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "喝水提醒")
        self.assertEqual(items[0]["tags"], ["健康"])

    def test_parse_dict_and_dedupe(self):
        reply = json.dumps(
            {
                "ideas": [
                    {"title": "A", "idea": "同一个点子", "tags": []},
                    {"title": "B", "idea": " 同一个点子 ", "tags": []},
                ]
            },
            ensure_ascii=False,
        )
        items = extractor.parse_ideas(reply)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "A")

    def test_parse_truncated_reply_salvages_complete_objects(self):
        reply = '[{"title":"A","idea":"第一个","tags":[]},{"title":"B","idea":"被截断'
        items = extractor.parse_ideas(reply)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["idea"], "第一个")

    def test_build_memory_messages_truncates_and_caps(self):
        memories = [{"text": "长" * 300} for _ in range(5)]
        messages = extractor.build_memory_messages(
            memories,
            system_prompt="SYS",
            intro="INTRO",
            ask="ASK",
            per_item_chars=20,
            max_items=3,
        )
        self.assertEqual(messages[0]["content"], "SYS")
        user = messages[1]["content"]
        self.assertIn("INTRO", user)
        self.assertIn("ASK", user)
        self.assertEqual(user.count("长" * 20 + "…"), 3)
        self.assertNotIn("4.", user)

    def test_generation_prompt_contains_theme_and_existing(self):
        messages = extractor.build_generation_messages("学习陪伴", 5, ["旧点子"])
        self.assertIn("学习陪伴", messages[1]["content"])
        self.assertIn("旧点子", messages[1]["content"])
        self.assertIn("3 条", messages[1]["content"])


class MemoryReaderTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.state = self.root / "plugins" / ".state"
        self.qq_dir = self.state / "lfelkeea.qq_bridge"
        self.alive_dir = self.state / "lfelkeea.alive_core"
        self.qq_dir.mkdir(parents=True)
        self.alive_dir.mkdir(parents=True)

    def test_loads_qq_memory_dedupes_and_skips_secrets(self):
        payload = {
            "items": [
                {"text": "今天好累", "created_at": time.time()},
                {"text": "今天好累"},
                {"text": "想做个提醒喝水的小工具"},
            ],
            "updated_at": time.time(),
        }
        (self.qq_dir / "memory.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (self.qq_dir / "settings.json").write_text(
            json.dumps({"token": "should-not-be-read", "text": "密钥"}, ensure_ascii=False),
            encoding="utf-8",
        )
        items, scanned = memory_reader.load_qq_memories(str(self.root))
        self.assertEqual([item["text"] for item in items], ["今天好累", "想做个提醒喝水的小工具"])
        self.assertTrue(all(item["source"] == memory_reader.QQ_SOURCE for item in items))
        self.assertEqual(len(scanned), 1)
        self.assertTrue(scanned[0].endswith("memory.json"))

    def test_loads_pet_episodes_and_excludes_qq_and_preferences(self):
        payload = {
            "episodes": [
                {"text": "主人：今天好累", "kind": "chat", "source": "pet", "at": 100},
                {"text": "QQ 聊天内容", "kind": "chat", "source": "qq", "at": 200},
                {"text": "屏幕观察：他在写代码", "kind": "screen", "source": "screen", "at": 300},
                {"text": "用户偏好", "kind": "preference", "source": "consolidation", "at": 400},
            ]
        }
        (self.alive_dir / "memory.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        items, scanned = memory_reader.load_pet_memories(str(self.root))
        self.assertEqual(
            [item["text"] for item in items],
            ["主人：今天好累", "屏幕观察：他在写代码"],
        )
        self.assertTrue(all(item["source"] == memory_reader.PET_SOURCE for item in items))
        self.assertEqual(len(scanned), 1)

    def test_live_conversation_snapshot_groups_roles(self):
        api = FakeAPI(
            conversation=FakeConversation(
                [
                    {"role": "user", "content": "今天好累"},
                    {"role": "assistant", "content": "那要不要发会儿呆"},
                    {"role": "user", "content": "好"},
                    {"role": "assistant", "content": "陪你"},
                ]
            )
        )
        items = memory_reader.live_conversation_items(api, limit=4, chunk=2)
        self.assertEqual(len(items), 2)
        self.assertIn("主人：今天好累", items[0]["text"])
        self.assertIn("桌宠：那要不要发会儿呆", items[0]["text"])
        self.assertEqual(items[0]["source"], memory_reader.PET_SOURCE)

    def test_live_conversation_missing_is_empty(self):
        self.assertEqual(memory_reader.live_conversation_items(SimpleNamespace()), [])

    def test_record_capture_dedupes_and_caps(self):
        api = FakeAPI(storage={})
        self.assertTrue(memory_reader.record_capture(api, "桌宠说了一句话", "chat", keep=2))
        self.assertFalse(memory_reader.record_capture(api, "桌宠说了一句话", "chat", keep=2))
        self.assertTrue(memory_reader.record_capture(api, "第二句话", "screen", keep=2))
        self.assertTrue(memory_reader.record_capture(api, "第三句话", "chat", keep=2))
        self.assertEqual([item["text"] for item in api.storage["pet_chat_log"]], ["第二句话", "第三句话"])
        self.assertGreaterEqual(api.saved, 3)

    def test_imported_text_is_chunked_and_capped(self):
        api = FakeAPI(storage={})
        text = "\n".join(f"第{i}行" for i in range(10))
        count = memory_reader.store_imported_text(api, text, keep=3)
        self.assertEqual(count, 2)
        self.assertEqual(len(api.storage["imported_chats"]), 2)
        items = memory_reader.imported_items(api)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], memory_reader.IMPORT_SOURCE)

        more = memory_reader.store_imported_text(api, "\n".join(f"新{i}行" for i in range(12)), keep=3)
        self.assertGreater(more, 0)
        self.assertEqual(len(api.storage["imported_chats"]), 3)

    def test_gather_pet_items_prioritizes_live_and_dedupes(self):
        (self.alive_dir / "memory.json").write_text(
            json.dumps(
                {"episodes": [{"text": "旧对话内容", "kind": "chat", "source": "pet", "at": 1}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        api = FakeAPI(
            storage={
                "pet_chat_log": [{"text": "旧对话内容", "kind": "screen"}],
                "imported_chats": [{"text": "导入的内容", "created": ""}],
            },
            conversation=FakeConversation([{"role": "user", "content": "当前对话"}]),
        )
        items, details = memory_reader.gather_pet_items(api, str(self.root), limit=10)
        self.assertEqual(items[0]["text"], "主人：当前对话")
        self.assertEqual(len([item for item in items if item["text"] == "旧对话内容"]), 1)
        self.assertEqual(details["live"], 1)
        self.assertEqual(details["captured"], 1)
        self.assertEqual(details["imported"], 1)
        self.assertEqual(details["alive"], 1)


class StoreTests(unittest.TestCase):
    def test_add_dedupe_counts_and_remove(self):
        api = FakeAPI(storage={})
        store = inspiration_store.InspirationStore(api)
        self.assertIsNone(store.add("", "   "))
        item = store.add("喝水", "提醒喝水", source="AI 生成", tags=["健康", "  "])
        self.assertIsNotNone(item)
        self.assertTrue(store.has_idea(" 提醒喝水 "))
        self.assertTrue(store.has_idea("提醒喝水"))
        self.assertEqual(store.source_counts(), {"AI 生成": 1})
        self.assertTrue(store.remove(item["id"]))
        self.assertFalse(store.remove(item["id"]))
        self.assertEqual(store.all(), [])

    def test_remove_many_deletes_selected_and_saves_once(self):
        api = FakeAPI(storage={})
        store = inspiration_store.InspirationStore(api)
        first = store.add("第一条", "第一条灵感")
        second = store.add("第二条", "第二条灵感")
        third = store.add("第三条", "第三条灵感")
        saved = api.saved

        self.assertEqual(store.remove_many([first["id"], third["id"], "missing"]), 2)
        self.assertEqual([item["id"] for item in store.all()], [second["id"]])
        self.assertEqual(api.saved, saved + 1)
        self.assertEqual(store.remove_many([]), 0)
        self.assertEqual(store.remove_many(None), 0)
        self.assertEqual(api.saved, saved + 1)


class PathTests(unittest.TestCase):
    def test_data_dir_is_plugin_parent_parent(self):
        from lfelkeea_qq_inspiration.service import _data_dir

        # 源码目录是 desktop_pet/plugins/<id>/<package>/service.py
        self.assertEqual(Path(_data_dir()).resolve(), ROOT.resolve())


class UISmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_open_settings_lists_existing_inspirations(self):
        from lfelkeea_qq_inspiration import service as plugin_main

        api = FakeAPI(
            storage={
                "inspirations": [
                    {
                        "id": "a1",
                        "title": "喝水提醒",
                        "idea": "提醒喝水",
                        "source": "AI 生成",
                        "tags": ["健康"],
                        "created": "2026-11-02 10:00:00",
                    }
                ]
            }
        )
        plugin_main.setup(api)
        dialog = plugin_main.open_settings(api)
        self.assertEqual(dialog.list_widget.count(), 1)
        self.assertIn("[AI]", dialog.list_widget.item(0).text())
        self.assertIn("共 1 条", dialog.count_label.text())
        self.assertIn("提醒喝水", dialog.detail.toPlainText())
        self.assertGreaterEqual(len(dialog.action_buttons), 6)
        dialog.close()
        self.app.processEvents()
        plugin_main.teardown()

    def test_one_click_deletes_multiple_selected_without_confirmation(self):
        from PySide6.QtWidgets import QAbstractItemView

        from lfelkeea_qq_inspiration import service as plugin_main

        items = [
            {
                "id": f"id{index}",
                "title": f"灵感{index}",
                "idea": f"点子{index}",
                "source": "AI 生成",
                "tags": [],
                "created": "2026-11-02 10:00:00",
            }
            for index in range(4)
        ]
        api = FakeAPI(storage={"inspirations": items})
        plugin_main.setup(api)
        dialog = plugin_main.open_settings(api)
        try:
            self.assertEqual(
                dialog.list_widget.selectionMode(), QAbstractItemView.ExtendedSelection
            )
            self.assertEqual(dialog.delete_button.text(), "一键删除（1）")

            dialog.list_widget.clearSelection()
            dialog.list_widget.item(0).setSelected(True)
            dialog.list_widget.item(2).setSelected(True)
            self.assertEqual(dialog.delete_button.text(), "一键删除（2）")

            dialog.delete_button.click()
            self.app.processEvents()

            self.assertEqual(dialog.list_widget.count(), 2)
            self.assertEqual(
                [item["id"] for item in api.storage["inspirations"]], ["id1", "id3"]
            )
            self.assertIn("已删除 2 条", dialog.status_label.text())
        finally:
            dialog.close()
            self.app.processEvents()
            plugin_main.teardown()


if __name__ == "__main__":
    unittest.main()
