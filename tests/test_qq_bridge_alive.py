from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import alive, text  # noqa: E402
from lfelkeea_qq_bridge import service as service_module  # noqa: E402
from lfelkeea_qq_bridge.service import QQBridgeService, normalize_settings  # noqa: E402


class FakeAPI:
    version = 1

    def __init__(self, state_dir: Path) -> None:
        self.settings: dict = {}
        self.logs: list[str] = []
        self.saved: list[dict] = []
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=state_dir)

    def say(self, message: str, duration: int = 5000) -> None:
        pass

    def log(self, message: str) -> None:
        self.logs.append(message)

    def save_settings(self, values: dict) -> None:
        self.saved.append(dict(values))
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        pass

    def chat(self, messages, callback) -> None:
        callback("好的。", None)


def write_export(root: Path, *, age: float = 0.0, memory_active: int = 3) -> Path:
    export = root / "lfelkeea.alive_core" / "export"
    export.mkdir(parents=True, exist_ok=True)
    context = {
        "version": 1,
        "generated_at": time.time(),
        "name": "喵呜",
        "state": {"label": "心情很好", "tone": "心情好，语气可以轻快"},
        "growth": "她最近学会了先听完再回答。",
        "rules": ["只使用给出的记忆，不要补充没记录的内容。"],
        "memory": {"active": memory_active, "episodes": 12},
    }
    (export / "context.json").write_text(
        json.dumps(context, ensure_ascii=False), encoding="utf-8"
    )
    items = [
        {
            "id": "m1",
            "text": "主人喜欢喝冰美式",
            "kind": "preference",
            "confidence": 0.8,
            "weight": 1.0,
            "half_life_days": 90,
            "created_at": time.time() - 60,
            "last_access": time.time() - 60,
            "tags": ["咖啡"],
            "visibility": "private",
        },
        {
            "id": "m2",
            "text": "群里在聊周末去爬山",
            "kind": "fact",
            "confidence": 0.6,
            "weight": 0.8,
            "half_life_days": 30,
            "created_at": time.time() - 120,
            "last_access": time.time() - 120,
            "tags": ["爬山"],
            "visibility": "group:123456",
        },
        {
            "id": "m3",
            "text": "好友小周养了一只橘猫",
            "kind": "fact",
            "confidence": 0.5,
            "weight": 0.7,
            "half_life_days": 30,
            "created_at": time.time() - 180,
            "last_access": time.time() - 180,
            "tags": ["猫"],
            "visibility": "private:22222",
        },
    ]
    (export / "memories.json").write_text(
        json.dumps({"version": 1, "items": items}, ensure_ascii=False),
        encoding="utf-8",
    )
    for path in export.iterdir():
        stamp = time.time() - age
        import os

        os.utime(path, (stamp, stamp))
    return root / "lfelkeea.alive_core"


class AliveLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = Path(tempfile.mkdtemp())
        self.core = write_export(self.state)
        self.link = alive.AliveLink(FakeAPI(self.state), lambda _message: None)

    def inbox(self) -> list[dict]:
        path = self.core / "inbox.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_keywords_and_overlap(self) -> None:
        self.assertIn("咖啡", alive.keywords("我今天喝了咖啡"))
        self.assertGreater(alive.overlap(alive.keywords("冰美式"), alive.keywords("喜欢冰美式")), 0)
        self.assertFalse(alive.keywords("的了吗呢"))

    def test_recall_respects_visibility(self) -> None:
        owner = self.link.recall("主人喜欢喝什么", viewer="owner")
        self.assertEqual([item["id"] for item in owner], ["m1"])
        group = self.link.recall("周末爬山", viewer="group:123456")
        self.assertEqual([item["id"] for item in group], ["m2"])
        other = self.link.recall("小周的橘猫", viewer="private:22222")
        self.assertEqual([item["id"] for item in other], ["m3"])
        # 别人的私聊记忆不会出现在主人视角。
        self.assertEqual(self.link.recall("小周的橘猫", viewer="owner"), [])

    def test_prompt_block_contains_state_and_memory(self) -> None:
        block, recalled = self.link.prompt_block("我还想喝冰美式", viewer="owner")
        self.assertIn("【她现在的状态】", block)
        self.assertIn("心情很好", block)
        self.assertIn("【她最近的自我认识】", block)
        self.assertIn("主人喜欢喝冰美式", block)
        self.assertIn("只使用这里写出的内容", block)
        self.assertEqual([item["id"] for item in recalled], ["m1"])

    def test_stale_export_is_unavailable(self) -> None:
        write_export(self.state, age=alive.STALE_SECONDS + 60)
        link = alive.AliveLink(FakeAPI(self.state), lambda _message: None)
        self.assertFalse(link.available())
        block, recalled = link.prompt_block("冰美式", viewer="owner")
        self.assertEqual(block, "")
        self.assertEqual(recalled, [])

    def test_disabled_switch_skips_everything(self) -> None:
        self.link.configure({"alive_enabled": False})
        self.assertFalse(self.link.available())
        self.assertFalse(self.link.report_episode("随便说点什么"))

    def test_report_writes_json_lines(self) -> None:
        self.assertTrue(
            self.link.report_episode(
                "主人说喜欢冰美式",
                actor_id="owner",
                visibility="private",
                conversation="private:10001",
                at=1700000000,
            )
        )
        self.assertTrue(self.link.report_recall(["m1"]))
        self.assertTrue(self.link.report_curiosity("周末去哪爬山"))
        rows = self.inbox()
        self.assertEqual(rows[0]["type"], "episode")
        self.assertEqual(rows[0]["text"], "主人说喜欢冰美式")
        self.assertEqual(rows[0]["visibility"], "private")
        self.assertEqual(rows[0]["at"], 1700000000)
        self.assertEqual(rows[1], {"type": "recall", "ids": ["m1"], "at": rows[1]["at"], "source": "qq"})
        self.assertEqual(rows[2]["topic"], "周末去哪爬山")

    def test_report_disabled(self) -> None:
        self.link.configure({"alive_report": False})
        self.assertFalse(self.link.report_episode("不该写进去"))
        self.assertEqual(self.inbox(), [])

    def test_status(self) -> None:
        status = self.link.status()
        self.assertTrue(status["available"])
        self.assertEqual(status["name"], "喵呜")
        self.assertEqual(status["state"], "心情很好")
        self.assertEqual(status["memories"], 3)
        self.assertEqual(status["recall_limit"], 5)


class ServiceAliveTests(unittest.TestCase):
    def make_service(self, settings: dict | None = None):
        state = Path(tempfile.mkdtemp())
        write_export(state)
        api = FakeAPI(state)
        with mock.patch.object(service_module, "SnowLumaManager"):
            service = QQBridgeService(api)
        service.settings = normalize_settings(
            {"owner_qq": "10001", "allow_groups": ["123456"], **dict(settings or {})}
        )
        service.alive.configure(service.settings)
        service.started = True
        return api, service

    def message(self, *, text_body: str, group: bool = False, user_id: str = "10001"):
        payload = {
            "post_type": "message",
            "message_type": "group" if group else "private",
            "message_id": "m-1",
            "self_id": "99999",
            "user_id": user_id,
            "message": [{"type": "text", "data": {"text": text_body}}],
            "time": int(time.time()),
            "sender": {"nickname": "主人"},
        }
        if group:
            payload["group_id"] = "123456"
        return text.parse_event(payload)

    def test_normalize_alive_settings(self) -> None:
        settings = normalize_settings(
            {"alive_enabled": "off", "alive_recall": "99", "alive_report": "on"}
        )
        self.assertFalse(settings["alive_enabled"])
        self.assertEqual(settings["alive_recall"], 10)
        self.assertTrue(settings["alive_report"])

    def test_system_prompt_includes_alive_block(self) -> None:
        api, service = self.make_service()
        message = self.message(text_body="我想喝冰美式")
        with mock.patch.object(service, "_start_chat") as start:
            service._reply_with_ai(message, message.text)
        system_prompt = start.call_args[0][1]
        self.assertIn("【她现在的状态】", system_prompt)
        self.assertIn("主人喜欢喝冰美式", system_prompt)
        self.assertEqual(service._pending, {"private:10001": service._pending["private:10001"]})

    def test_alive_disabled_keeps_old_prompt(self) -> None:
        api, service = self.make_service({"alive_enabled": False})
        message = self.message(text_body="我想喝冰美式")
        with mock.patch.object(service, "_start_chat") as start:
            service._reply_with_ai(message, message.text)
        system_prompt = start.call_args[0][1]
        self.assertNotIn("【她现在的状态】", system_prompt)

    def test_incoming_reports_owner_episode(self) -> None:
        api, service = self.make_service()
        message = self.message(text_body="今天有点累", group=True)
        service._report_incoming(message, message.text)
        rows = json.loads((service.alive.inbox_path).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rows["type"], "episode")
        self.assertEqual(rows["actor_id"], "owner")
        self.assertEqual(rows["visibility"], "private")
        self.assertEqual(rows["kind"], "group")

    def test_non_owner_group_uses_group_visibility(self) -> None:
        api, service = self.make_service({"watch_users": ["22222"]})
        message = self.message(text_body="周末去爬山", group=True, user_id="22222")
        service._report_incoming(message, message.text)
        rows = json.loads((service.alive.inbox_path).read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(rows["actor_id"], "other")
        self.assertEqual(rows["visibility"], "group:123456")

    def test_report_reply_marks_recall_and_curiosity(self) -> None:
        api, service = self.make_service()
        message = self.message(text_body="你还记得我喜欢喝什么吗？")
        with mock.patch.object(service.alive, "report") as report:
            service._report_reply(message, "记不清了，你再告诉我一次？", [{"id": "m1"}])
        calls = [call.args[0] for call in report.call_args_list]
        self.assertEqual(calls[0], {"type": "recall", "ids": ["m1"]})
        self.assertEqual(calls[1]["type"], "episode")
        self.assertEqual(calls[1]["actor_id"], "self")
        self.assertEqual(calls[2], {"type": "affect", "event": "bot_spoke"})
        self.assertEqual(calls[3]["type"], "curiosity")
        self.assertIn("记得", calls[3]["topic"])

    def test_command_memory_writes_through(self) -> None:
        api, service = self.make_service()
        conversation = "private:10001"
        with mock.patch.object(service, "_reply_local"), mock.patch.object(
            service.alive, "report_remember"
        ) as remember, mock.patch.object(service.alive, "report_forget") as forget:
            from lfelkeea_qq_bridge.commands import parse_command

            service._handle_command(parse_command("/memory add 主人喜欢冰美式"), self.message(text_body="/memory add 主人喜欢冰美式"))
            service._handle_command(parse_command("/memory del 冰美式"), self.message(text_body="/memory del 冰美式"))
        remember.assert_called_once_with("主人喜欢冰美式", visibility="private")
        forget.assert_called_once_with("冰美式")


if __name__ == "__main__":
    unittest.main()
