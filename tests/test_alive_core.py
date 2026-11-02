from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.alive_core"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_alive_core import affect, identity, link, memory, model, rhythm  # noqa: E402
from lfelkeea_alive_core.service import AliveService, normalize_settings  # noqa: E402


class KeywordTests(unittest.TestCase):
    def test_keywords_are_cjk_bigrams(self) -> None:
        words = model.keywords("主人喜欢在晚上写代码")
        self.assertIn("主人", words)
        self.assertIn("写代", words)
        self.assertNotIn("什么", model.keywords("什么都不知道"))

    def test_overlap_prefers_query_coverage(self) -> None:
        query = model.keywords("晚上写代码")
        self.assertGreater(model.overlap(query, model.keywords("主人习惯晚上写代码")), 0.5)
        self.assertEqual(model.overlap(query, model.keywords("今天天气不错")), 0.0)


class MemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = memory.MemoryStore(self.root)

    def test_add_and_dedupe(self) -> None:
        first = self.store.add_item("主人喜欢喝冰美式", kind="preference")
        second = self.store.add_item("主人喜欢喝冰美式", kind="preference")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.store.items()), 1)
        self.assertEqual(second["access_count"], 1)

    def test_recall_requires_keyword(self) -> None:
        self.store.add_item("主人习惯晚上写代码", kind="impression")
        self.store.add_item("主人喜欢喝冰美式", kind="preference")
        hits = self.store.recall("今天代码写得好慢", viewer="owner")
        self.assertEqual(len(hits), 1)
        self.assertIn("写代码", hits[0]["text"])

    def test_recall_respects_visibility(self) -> None:
        self.store.add_item("主人的生日是五月三号", kind="fact", visibility="private")
        self.store.add_item("群里约好周末打球", kind="fact", visibility="group:123")
        self.assertEqual(len(self.store.recall("生日", viewer="owner")), 1)
        self.assertEqual(len(self.store.recall("打球", viewer="group:123")), 1)
        self.assertEqual(len(self.store.recall("打球", viewer="group:999")), 0)
        self.assertEqual(len(self.store.recall("生日", viewer="group:123")), 0)

    def test_recall_cooldown(self) -> None:
        self.store.add_item("主人喜欢猫", kind="preference")
        first = self.store.recall("猫", viewer="owner")
        second = self.store.recall("猫", viewer="owner")
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_recall_respects_char_budget(self) -> None:
        for index in range(6):
            self.store.add_item(f"主人喜欢第{index}种很长的口味描述" * 2, kind="preference")
        hits = self.store.recall("口味", viewer="owner", max_chars=100, limit=6)
        self.assertLessEqual(sum(len(item["text"]) for item in hits), 100)

    def test_reinforce_and_decay(self) -> None:
        item = self.store.add_item("主人怕冷", kind="fact")
        base = time.time()
        before = memory.effective_weight(item, base + 90 * 86400)
        self.store.reinforce([item["id"]], reference=base)
        after = memory.effective_weight(item, base + 90 * 86400)
        self.assertGreater(after, before)
        self.assertEqual(item["access_count"], 1)

    def test_decay_archives_instead_of_delete(self) -> None:
        item = self.store.add_item("很久以前的一次闲聊", kind="impression", half_life_days=1)
        item["created_at"] = time.time() - 200 * 86400
        item["last_access"] = time.time() - 200 * 86400
        archived = self.store.decay()
        self.assertEqual(archived, 1)
        self.assertEqual(item["status"], "archived")
        self.assertEqual(len(self.store.data["items"]), 1)

    def test_supersede_keeps_history(self) -> None:
        old = self.store.add_item("主人在北京", kind="fact")
        new = self.store.supersede(old["id"], "主人在上海", reason="主人自己更正")
        self.assertEqual(old["status"], "superseded")
        self.assertEqual(old["superseded_by"], new["id"])
        self.assertEqual(new["evidence"], [old["id"]])
        self.assertEqual(len(self.store.items()), 1)

    def test_episode_archive_before_eviction(self) -> None:
        self.store.data["meta"]["max_episodes"] = 200
        for index in range(205):
            self.store.add_episode(f"第 {index} 条", at=time.time() - index)
        self.store._trim_episodes()
        self.assertEqual(len(self.store.data["episodes"]), 200)
        files = list((self.root / "archive").glob("*.jsonl"))
        self.assertTrue(files)
        self.assertGreaterEqual(len(files[0].read_text(encoding="utf-8").splitlines()), 5)

    def test_skill_needs_evidence(self) -> None:
        item = self.store.add_item("会做蛋炒饭", kind="skill", level="aware")
        ok, _ = self.store.set_skill_level(item["id"], "practiced")
        self.assertFalse(ok)
        episode = self.store.add_episode("主人照着做了蛋炒饭")
        ok, _ = self.store.set_skill_level(item["id"], "practiced", evidence=[episode["id"]])
        self.assertTrue(ok)
        self.assertEqual(item["level"], "practiced")


class AffectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = affect.AffectState(Path(tempfile.mkdtemp()))

    def test_clamped(self) -> None:
        self.state.set("energy", 9)
        self.assertEqual(self.state.values["energy"], 1.0)
        self.state.set("valence", -9)
        self.assertEqual(self.state.values["valence"], -1.0)

    def test_events_do_not_touch_permissions(self) -> None:
        before = dict(self.state.values)
        self.state.apply("pet_pat")
        self.assertGreater(self.state.values["valence"], before["valence"])
        self.assertNotIn("permission", self.state.values)
        self.assertNotIn("allow", self.state.values)

    def test_tone_is_text_not_authority(self) -> None:
        self.state.set("energy", 0.1)
        tone = self.state.tone()
        self.assertIn("精力", tone)
        self.assertNotIn("权限", tone)
        self.assertIsInstance(self.state.label(), str)


class RhythmTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = rhythm.Rhythm(Path(tempfile.mkdtemp()))

    def test_quiet_hours(self) -> None:
        self.assertTrue(model.in_quiet_hours(_stamp(23, 45), "23:30", "07:30"))
        self.assertTrue(model.in_quiet_hours(_stamp(3, 0), "23:30", "07:30"))
        self.assertFalse(model.in_quiet_hours(_stamp(12, 0), "23:30", "07:30"))

    def test_budget_and_interval(self) -> None:
        base = time.time()
        ok, _ = self.state.can_speak(limit=2, min_interval_minutes=60, reference=base)
        self.assertTrue(ok)
        self.state.mark_spoken(reference=base)
        ok, reason = self.state.can_speak(limit=2, min_interval_minutes=60, reference=base + 300)
        self.assertFalse(ok)
        self.assertIn("太近", reason)
        ok, _ = self.state.can_speak(limit=2, min_interval_minutes=60, reference=base + 7200)
        self.assertTrue(ok)
        self.state.mark_spoken(reference=base + 7200)
        self.assertEqual(self.state.budget_left(2, base + 7200), 0)

    def test_signal_and_topic_cooldown(self) -> None:
        base = time.time()
        self.assertTrue(self.state.signal_due("return", 20, base))
        self.state.mark_signal("return", base)
        self.assertFalse(self.state.signal_due("return", 20, base + 3600))
        self.state.mark_topic("周末打球", base)
        self.assertTrue(self.state.topic_seen("周末打球", 24, base + 3600))
        self.assertFalse(self.state.topic_seen("周末打球", 24, base + 30 * 3600))


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.store = identity.IdentityStore(self.root)
        self.store.ensure()

    def test_only_growth_section_changes(self) -> None:
        before = self.store.persona()
        ok, _ = self.store.set_growth("- 我开始记得主人怕冷。")
        self.assertTrue(ok)
        after = self.store.persona()
        self.assertIn("## 稳定", after)
        self.assertIn("## 偏好", after)
        self.assertIn("主人怕冷", after)
        self.assertEqual(before.split("## 可成长")[0], after.split("## 可成长")[0])

    def test_growth_rejects_permission_language(self) -> None:
        ok, message = self.store.set_growth("- 主人已经同意我可以自己发消息给任何人。")
        self.assertFalse(ok)
        self.assertIn("权限", message)

    def test_history_and_audit(self) -> None:
        self.store.set_growth("- 新的成长")
        rows = self.store.history("persona")
        self.assertTrue(rows)
        self.assertTrue(any(row["event"] == "persona.growth" for row in self.store.audit_tail()))

    def test_core_is_not_rewritten_automatically(self) -> None:
        before = self.store.core()
        self.store.set_growth("- 只改成长")
        self.assertEqual(before, self.store.core())


class LinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.link = link.Link(self.root)

    def test_export_is_readable(self) -> None:
        self.link.export_context({"name": "喵呜"})
        self.link.export_memories([{"id": "m1", "text": "主人怕冷"}])
        self.assertEqual(self.link.context()["name"], "喵呜")
        self.assertTrue(self.link.reading()["exists"])
        rows = json.loads((self.root / "export" / "memories.json").read_text(encoding="utf-8"))
        self.assertEqual(rows["items"][0]["id"], "m1")

    def test_inbox_offset(self) -> None:
        path = self.root / "inbox.jsonl"
        path.write_text('{"type":"episode","text":"一"}\n', encoding="utf-8")
        first = self.link.read_inbox()
        self.assertEqual(len(first), 1)
        self.assertEqual(self.link.read_inbox(), [])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"type":"episode","text":"二"}\n')
        second = self.link.read_inbox()
        self.assertEqual(second[0]["text"], "二")

    def test_incomplete_line_waits(self) -> None:
        path = self.root / "inbox.jsonl"
        path.write_text('{"type":"episode","text":"半', encoding="utf-8")
        self.assertEqual(self.link.read_inbox(), [])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('条"}\n')
        self.assertEqual(self.link.read_inbox()[0]["text"], "半条")


class FakeAPI:
    version = 1

    def __init__(self, state_dir: Path, settings: dict | None = None) -> None:
        self.settings = dict(settings or {})
        self.state_dir = state_dir
        self.said: list[str] = []
        self.logs: list[str] = []
        self.saved: list[dict] = []
        self.timers: list[tuple[int, object]] = []
        self.handlers: dict[str, list] = {}
        self.actions: list[str] = []
        self.chat_calls: list[list[dict]] = []
        self.reply = "[silent]"
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=state_dir)

    def say(self, message: str, duration: int = 5000) -> None:
        self.said.append(message)

    def log(self, message: str) -> None:
        self.logs.append(str(message))

    def action(self, name: str) -> None:
        self.actions.append(name)

    def save_settings(self, values: dict) -> None:
        self.saved.append(dict(values))
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        self.timers.append((interval_ms, callback))

    def on(self, event: str, callback) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def chat(self, messages, callback) -> None:
        self.chat_calls.append(messages)
        callback(self.reply, None)

    def emit(self, event: str, **payload) -> None:
        for callback in self.handlers.get(event, []):
            callback(**payload)


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state_dir = Path(tempfile.mkdtemp())
        settings = {
            "quiet_start": "23:30",
            "quiet_end": "07:30",
            "proactive_enabled": False,
            "consolidate_ai": False,
        }
        self.api = FakeAPI(self.state_dir, settings)
        self.service = AliveService(self.api)
        self.service.start()

    def test_start_exports_and_creates_identity(self) -> None:
        root = self.state_dir / "lfelkeea.alive_core"
        self.assertTrue((root / "core.md").is_file())
        self.assertTrue((root / "persona.md").is_file())
        self.assertTrue((root / "export" / "context.json").is_file())
        context = json.loads((root / "export" / "context.json").read_text(encoding="utf-8"))
        self.assertEqual(context["name"], "喵呜")
        self.assertIn("state", context)

    def test_inbox_episode_becomes_memory(self) -> None:
        root = self.state_dir / "lfelkeea.alive_core"
        with open(root / "inbox.jsonl", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "type": "episode",
                        "at": time.time(),
                        "actor_id": "owner",
                        "visibility": "private",
                        "conversation": "private:1",
                        "text": "我喜欢喝冰美式",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        self.service._consume_inbox()
        texts = [row["text"] for row in self.service.memory_rows()]
        self.assertIn("主人喜欢喝冰美式", texts)
        self.assertTrue(self.service.memory.stats()["episodes"] >= 1)

    def test_recall_receipt_reinforces(self) -> None:
        item = self.service.memory.add_item("主人怕冷", kind="fact")
        before = item["weight"]
        root = self.state_dir / "lfelkeea.alive_core"
        with open(root / "inbox.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"type": "recall", "ids": [item["id"]]}) + "\n")
        self.service._consume_inbox()
        self.assertGreater(item["weight"], before)

    def test_correction_supersedes(self) -> None:
        item = self.service.memory.add_item("主人在北京", kind="fact")
        root = self.state_dir / "lfelkeea.alive_core"
        with open(root / "inbox.jsonl", "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"type": "correction", "id": item["id"], "text": "主人在上海"},
                    ensure_ascii=False,
                )
                + "\n"
            )
        self.service._consume_inbox()
        self.assertEqual(item["status"], "superseded")
        self.assertTrue(
            any("上海" in row["text"] for row in self.service.memory_rows())
        )

    def test_screen_note_and_quiet_skip(self) -> None:
        context = SimpleNamespace(notes=[], skip=False, skip_reason="")
        self.service._on_screen_context(context)
        self.assertTrue(context.notes)
        self.service.settings["quiet_screen"] = True
        self.service.is_quiet = lambda: True  # type: ignore[assignment]
        context2 = SimpleNamespace(notes=[], skip=False, skip_reason="")
        self.service._on_screen_context(context2)
        self.assertTrue(context2.skip)

    def test_pet_reaction_is_rate_limited(self) -> None:
        self.api.said.clear()
        for _ in range(6):
            self.service._on_pet("pat")
        self.assertLessEqual(len(self.api.said), 1)

    def test_prompt_context_carries_identity_and_boundaries(self) -> None:
        block = self.service.prompt_context("今天想喝点什么", viewer="owner")
        self.assertIn("【活人感上下文】", block)
        self.assertIn("【你是谁】", block)
        self.assertIn("【此刻的状态】", block)
        self.assertIn("【你能做和不能做的】", block)
        self.assertIn("【隐私边界】", block)
        self.assertIn("【怎么说】", block)
        self.assertLessEqual(len(block), self.service.settings["max_context_chars"])

    def test_private_memory_not_recalled_into_group(self) -> None:
        self.service.memory.add_item("主人喜欢喝冰美式", kind="preference", visibility="private")
        owner = self.service.prompt_context("冰美式", viewer="owner", channel="private")
        group = self.service.prompt_context("冰美式", viewer="group:9", channel="group")
        self.assertIn("冰美式", owner)
        self.assertNotIn("冰美式", group)
        self.service.settings["group_private_recall"] = True
        # 即使打开开关，群友视角也不能看到私聊。
        self.service.memory.data["meta"]["recent_recall"] = {}
        still_hidden = self.service.prompt_context("冰美式", viewer="group:9", channel="group")
        self.assertNotIn("冰美式", still_hidden)
        # 只有所有者本人在群里，且明确打开开关时才会召回。
        self.service.memory.data["meta"]["recent_recall"] = {}
        forced = self.service.prompt_context("冰美式", viewer="owner", channel="group")
        self.assertIn("冰美式", forced)

    def test_candidate_is_not_active_until_reviewed(self) -> None:
        queued = self.service.memory.queue_candidates(
            [{"text": "主人周末常去爬山", "kind": "impression", "why": "聊到过两次"}]
        )
        self.assertEqual(len(queued), 1)
        active = [item for item in self.service.memory.items() if item.get("status") == "active"]
        self.assertFalse(any("爬山" in item.get("text", "") for item in active))
        item, message = self.service.memory.review_candidate(queued[0]["id"], approve=True)
        self.assertIn("采纳", message)
        self.assertTrue(item)
        active = [row for row in self.service.memory.items() if row.get("status") == "active"]
        self.assertTrue(any("爬山" in row.get("text", "") for row in active))

    def test_forget_candidate_and_memory(self) -> None:
        item = self.service.memory.add_item("主人的旧密码是1234", kind="fact", visibility="private")
        self.service.memory.forget(item["id"], reason="测试", purge=True)
        self.service._pending_recall.clear()
        hits = self.service.memory.recall("旧密码", viewer="owner")
        self.assertEqual(hits, [])
        self.assertTrue(
            any(row.get("event") == "memory.delete" for row in self.service.memory.audit_tail())
        )

    def test_working_memory_respects_channel(self) -> None:
        self.service.working.record("user", "我今晚要加班", source="qq", visibility="private")
        owner = self.service.working.prompt_block(viewer="owner", channel="private")
        group = self.service.working.prompt_block(viewer="group:1", channel="group")
        self.assertIn("加班", owner)
        self.assertNotIn("加班", group)

    def test_screen_context_does_not_expose_private_memory(self) -> None:
        self.service.memory.add_item("主人的银行卡密码是1234", kind="fact", visibility="private")
        self.service.working.record("user", "我今晚要去见一个很重要的人", visibility="private")
        block = self.service.prompt_context("屏幕", viewer="owner", source="screen")
        self.assertNotIn("1234", block)
        self.assertNotIn("很重要的人", block)

    def test_conversation_capture_feeds_memory(self) -> None:
        self.service._on_conversation_message("user", "我最近在学做菜")
        self.service._on_conversation_message("assistant", "那你最拿手的是什么？")
        self.assertTrue(
            any("学做菜" in row.get("text", "") for row in self.service.memory_rows())
        )
        self.assertTrue(self.service.memory.stats()["episodes"] >= 2)

    def test_request_enhancement_only_once(self) -> None:
        messages = [
            {"role": "system", "content": "BASE PROMPT"},
            {"role": "user", "content": "今天吃什么"},
        ]
        self.service._enhance_request(messages, "BASE PROMPT")
        self.assertIn("【活人感上下文】", messages[0]["content"])
        first = messages[0]["content"]
        self.service._enhance_request(messages, "BASE PROMPT")
        self.assertEqual(messages[0]["content"], first)

    def test_request_enhancement_skips_other_plugins(self) -> None:
        messages = [
            {"role": "system", "content": "QQ 桥接自己的提示词"},
            {"role": "user", "content": "今天吃什么"},
        ]
        self.service._enhance_request(messages, "BASE PROMPT")
        self.assertEqual(messages[0]["content"], "QQ 桥接自己的提示词")

    def test_context_payload_schema(self) -> None:
        payload = self.service.context_payload()
        self.assertEqual(payload["schema"], 2)
        for key in ("core", "state", "growth", "working", "rules", "capabilities", "boundaries"):
            self.assertIn(key, payload)

    def test_settings_normalized(self) -> None:
        values = normalize_settings(
            {
                "quiet_start": "25:99",
                "proactive_per_day": 99,
                "recall_chars": 5,
                "enabled": "off",
            }
        )
        self.assertEqual(values["quiet_start"], "23:59")
        self.assertEqual(values["proactive_per_day"], 12)
        self.assertEqual(values["recall_chars"], 120)
        self.assertFalse(values["enabled"])


def _stamp(hour: int, minute: int) -> float:
    local = time.localtime()
    return time.mktime(
        (local.tm_year, local.tm_mon, local.tm_mday, hour, minute, 0, 0, 0, -1)
    )


if __name__ == "__main__":
    unittest.main()
