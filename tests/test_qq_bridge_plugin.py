from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.qq_bridge"
sys.path.insert(0, str(PLUGIN))

from lfelkeea_qq_bridge import analysis, commands, onebot, store, text  # noqa: E402
from lfelkeea_qq_bridge.service import (  # noqa: E402
    QQBridgeService,
    _format_qzone,
    _parse_allow_target,
    normalize_settings,
)


class FakeAPI:
    version = 1

    def __init__(self, settings: dict | None = None, state_dir: Path | None = None) -> None:
        self.settings = dict(settings or {})
        self.said: list[str] = []
        self.logs: list[str] = []
        self.saved: list[dict] = []
        self.timers: list[tuple[int, object]] = []
        self.chat_calls: list[list[dict]] = []
        self.reply = "好的。"
        self.chat_error: str | None = None
        self.info = SimpleNamespace(folder=PLUGIN)
        self.manager = SimpleNamespace(state_dir=state_dir or Path(tempfile.mkdtemp()))

    def say(self, message: str, duration: int = 5000) -> None:
        self.said.append(message)

    def log(self, message: str) -> None:
        self.logs.append(message)

    def save_settings(self, values: dict) -> None:
        self.saved.append(dict(values))
        self.settings.update(values)

    def every(self, interval_ms: int, callback) -> None:
        self.timers.append((interval_ms, callback))

    def chat(self, messages, callback) -> None:
        self.chat_calls.append(messages)
        callback(self.reply, self.chat_error)


class FakeClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[dict]]] = []
        self.qzone: list[dict] = []
        self.event = threading.Event()

    def send(self, conversation: str, segments: list[dict]) -> None:
        self.sent.append((conversation, segments))
        self.event.set()

    def get_login_info(self) -> dict:
        return {"user_id": 99999, "nickname": "小猫"}

    def publish_qzone(self, content: str, ugc_right: int = 4, images=None, target_uins=None) -> None:
        self.qzone.append(
            {
                "content": content,
                "ugc_right": ugc_right,
                "images": list(images or []),
                "target_uins": list(target_uins or []),
            }
        )
        self.event.set()

    def list_qzone(self, num: int = 20) -> list[dict]:
        self.event.set()
        return [{"tid": "t1", "content": "今天的猫"}]

    def delete_qzone(self, tid: str) -> None:
        self.qzone.append({"delete": tid})
        self.event.set()


def group_event(text_body: str = "在吗", *, message_id: int = 1) -> dict:
    return {
        "post_type": "message",
        "message_type": "group",
        "group_id": 123456,
        "user_id": 10001,
        "self_id": 99999,
        "message_id": message_id,
        "time": 1700000000,
        "sender": {"card": "小明"},
        "message": [
            {"type": "at", "data": {"qq": "99999"}},
            {"type": "text", "data": {"text": " " + text_body}},
        ],
    }


def private_event(text_body: str, *, user_id: int = 10001, message_id: int = 2) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "user_id": user_id,
        "self_id": 99999,
        "message_id": message_id,
        "time": 1700000000,
        "sender": {"nickname": "主人"},
        "message": [{"type": "text", "data": {"text": text_body}}],
    }


class TextTests(unittest.TestCase):
    def test_parse_array_message(self) -> None:
        message = text.parse_event(group_event("在吗"))
        assert message is not None
        self.assertTrue(message.is_group)
        self.assertTrue(message.at_self)
        self.assertEqual(message.conversation, "group:123456")
        self.assertEqual(message.sender_name, "小明")
        self.assertIn("在吗", message.text)

    def test_parse_cq_string(self) -> None:
        payload = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 1,
            "user_id": 2,
            "self_id": 999,
            "message": "[CQ:at,qq=999] [CQ:face,id=14] 你好",
        }
        message = text.parse_event(payload)
        assert message is not None
        self.assertTrue(message.at_self)
        self.assertEqual(message.face_count, 1)
        self.assertIn("[face:14]", message.text)

    def test_ignore_sent_by_default(self) -> None:
        payload = group_event()
        payload["post_type"] = "message_sent"
        self.assertIsNone(text.parse_event(payload))
        self.assertIsNotNone(text.parse_event(payload, include_sent=True))

    def test_is_addressed(self) -> None:
        message = text.parse_event(group_event())
        assert message is not None
        self.assertTrue(text.is_addressed(message, "小猫"))
        plain = group_event("随便说说")
        plain["message"] = [{"type": "text", "data": {"text": "随便说说"}}]
        parsed = text.parse_event(plain)
        assert parsed is not None
        self.assertFalse(text.is_addressed(parsed, "小猫"))
        self.assertTrue(
            text.is_addressed(parsed, "小猫", group_trigger="all", reply_chance=1.0)
        )

    def test_sanitize_and_segments(self) -> None:
        self.assertEqual(text.sanitize_reply("[silent]"), "")
        cleaned = text.sanitize_reply("**你好**\n- 在的\n```py\nx\n```", 100)
        self.assertIn("你好", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertNotIn("```", cleaned)
        segments = text.build_segments("你好", ["14"], ["C:/a.png"])
        self.assertEqual(segments[1], {"type": "face", "data": {"id": "14"}})
        self.assertEqual(segments[2]["type"], "image")

    def test_split_and_clean(self) -> None:
        self.assertEqual(text.split_text(""), [])
        chunks = text.split_text("a" * 500, 200)
        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))
        value = text.clean_incoming_text("@小猫 你好[face:14]", "小猫")
        self.assertEqual(value, "你好")


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.store = store.JsonStore(self.tmp, PLUGIN / "persona.md")

    def test_persona_default_and_update(self) -> None:
        self.assertIn("小猫", self.store.persona())
        self.store.set_persona("新人格")
        self.assertEqual(self.store.persona(), "新人格")
        with self.assertRaises(ValueError):
            self.store.set_persona("")

    def test_memory_dedupe_and_remove(self) -> None:
        first = self.store.add_memory("主人喜欢猫", importance=50)
        second = self.store.add_memory("主人喜欢猫", importance=70)
        assert first is not None and second is not None
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["hits"], 2)
        self.assertEqual(len(self.store.memories()), 1)
        self.assertEqual(self.store.remove_memory("喜欢猫"), 1)
        self.assertEqual(self.store.memories(), [])

    def test_slang(self) -> None:
        self.store.add_slang("好耶", "表示赞同", "好耶！")
        self.store.add_slang("好耶", "非常赞同")
        terms = self.store.slang_terms()
        self.assertEqual(terms["好耶"]["count"], 2)
        self.assertEqual(terms["好耶"]["meaning"], "非常赞同")
        self.assertTrue(self.store.remove_slang("好耶"))
        self.assertFalse(self.store.remove_slang("好耶"))

    def test_habits_and_runtime(self) -> None:
        self.store.observe("group:1", "在吗？", face_count=1, hour=21)
        habit = self.store.habit("group:1")
        self.assertEqual(habit["count"], 1)
        self.assertEqual(habit["question"], 1)
        self.assertEqual(habit["faces"], 1)
        self.assertEqual(habit["hours"][21], 1)

        self.store.append_recent("group:1", "user", "在吗", name="小明")
        recent = self.store.recent("group:1")
        self.assertEqual(recent[-1]["name"], "小明")

        self.store.append_inbox("group:1", "user", "在吗", name="小明")
        self.assertEqual(len(self.store.inbox()), 1)
        self.store.clear_inbox(keep=0)
        self.assertEqual(self.store.inbox(), [])

        self.store.mark_seen("m1")
        self.assertTrue(self.store.seen("m1"))
        self.store.mark_compacted()
        self.store.flush()
        reloaded = store.JsonStore(self.tmp, PLUGIN / "persona.md")
        self.assertGreater(reloaded.runtime["last_compact_at"], 0)


class AnalysisTests(unittest.TestCase):
    def test_habit_summary(self) -> None:
        habit = {
            "count": 10,
            "chars": 80,
            "short": 8,
            "question": 4,
            "exclaim": 1,
            "ellipsis": 0,
            "faces": 5,
            "hours": [0] * 24,
            "samples": ["在吗", "好耶"],
        }
        habit["hours"][20] = 6
        summary = analysis.habit_summary(habit)
        self.assertIn("10 条", summary)
        self.assertIn("短句", summary)
        self.assertIn("表情", summary)
        self.assertGreater(analysis.habit_face_chance(habit), 0.2)

    def test_prompts(self) -> None:
        memory = analysis.memory_prompt([{"text": "主人喜欢猫"}])
        self.assertIn("主人喜欢猫", memory)
        terms = {
            "好耶": {"term": "好耶", "meaning": "赞同", "count": 3},
            "典": {"term": "典", "meaning": "经典", "count": 1},
        }
        slang = analysis.slang_prompt(terms, "好耶")
        self.assertTrue(slang.startswith("- 好耶"))
        prompt = analysis.build_system_prompt("人格", memory, slang, "习惯", 1200)
        self.assertIn("人格", prompt)
        self.assertIn("输出规则", prompt)
        self.assertLessEqual(len(prompt), 1400)

    def test_compaction_parsing(self) -> None:
        raw = '```json\n{"memories":["主人养猫"],"slang":[{"term":"好耶","meaning":"赞同"},{"term":"哈哈","meaning":"笑"}]}\n```'
        memories, slang = analysis.parse_compaction(raw)
        self.assertEqual(memories, ["主人养猫"])
        self.assertEqual(len(slang), 1)
        self.assertEqual(slang[0]["term"], "好耶")
        prompt = analysis.compaction_prompt(
            [{"name": "小明", "role": "user", "text": "好耶"}], ["旧词"]
        )
        self.assertIn("小明：好耶", prompt)
        self.assertIn("旧词", prompt)


class CommandTests(unittest.TestCase):
    def test_parse(self) -> None:
        command = commands.parse_command("/allow add group:1", "/")
        assert command is not None
        self.assertEqual(command.name, "allow")
        self.assertEqual(command.args, ["add", "group:1"])
        self.assertIsNone(commands.parse_command("allow add", "/"))
        self.assertIsNotNone(commands.parse_command("／help", "/"))
        self.assertIn("/status", commands.help_text("/"))


class OneBotServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[dict] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                self.server.seen.append(
                    {
                        "path": self.path,
                        "auth": self.headers.get("Authorization", ""),
                        "body": body,
                    }
                )
                if body.get("fail"):
                    payload = {"status": "failed", "retcode": 100, "wording": "坏了"}
                else:
                    payload = {"status": "ok", "retcode": 0, "data": {"user_id": 1}}
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.seen = self.seen
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def test_call_and_qzone(self) -> None:
        client = onebot.OneBotClient(self.base, "token")
        data = client.get_login_info()
        self.assertEqual(data["user_id"], 1)
        self.assertEqual(self.seen[-1]["path"], "/get_login_info")
        self.assertEqual(self.seen[-1]["auth"], "Bearer token")

        client.publish_qzone("你好", 16, ["a.png"], ["123"])
        body = self.seen[-1]["body"]
        self.assertEqual(self.seen[-1]["path"], "/send_qzone_msg")
        self.assertEqual(body["ugc_right"], 16)
        self.assertEqual(body["images"], ["a.png"])
        self.assertEqual(body["target_uins"], ["123"])

    def test_error_raises(self) -> None:
        client = onebot.OneBotClient(self.base)
        with self.assertRaises(onebot.OneBotError):
            client.call("send_group_msg", {"fail": True})

    def test_event_server_signature(self) -> None:
        received: list[dict] = []
        server = onebot.EventServer(
            "127.0.0.1", 0, "/hook", "secret", on_event=received.append
        )
        port = server.start(port_attempts=1)
        try:
            url = f"http://127.0.0.1:{port}/hook"
            with urllib.request.urlopen(url, timeout=3) as response:
                self.assertEqual(response.status, 200)

            payload = json.dumps({"post_type": "message"}).encode()
            request = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

            signature = hmac.new(b"secret", payload, hashlib.sha1).hexdigest()
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "X-Signature": signature},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)

            # SnowLuma 实际发送的是 "sha1=<hex>"，必须和裸十六进制都兼容。
            payload = json.dumps({"post_type": "message", "seq": 2}).encode()
            signature = "sha1=" + hmac.new(b"secret", payload, hashlib.sha1).hexdigest()
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "X-Signature": signature},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            self.assertEqual(
                received, [{"post_type": "message"}, {"post_type": "message", "seq": 2}]
            )

            payload = json.dumps({"post_type": "message", "seq": 3}).encode()
            request = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "X-Signature": "sha1=deadbeef"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()
        finally:
            server.stop()


class ServiceTests(unittest.TestCase):
    def make_service(self, settings: dict | None = None):
        api = FakeAPI(
            {
                "owner_qq": "10001",
                "allow_groups": ["123456"],
                "auto_face": False,
                "face_probability": 0,
                "sticker_probability": 0,
                **dict(settings or {}),
            }
        )
        service = QQBridgeService(api)
        service.client = FakeClient()
        service.started = True
        return api, service

    def test_normalize_settings(self) -> None:
        settings = normalize_settings(
            {
                "http_port": "99999",
                "reply_chance": "9",
                "allow_private": "123, 456，789",
                "face_ids": ["14", "x", "999"],
                "qzone_permission": "乱写",
                "group_trigger": "all",
                "memory_enabled": "off",
            }
        )
        self.assertEqual(settings["http_port"], 65535)
        self.assertEqual(settings["reply_chance"], 1.0)
        self.assertEqual(settings["allow_private"], ["123", "456", "789"])
        self.assertEqual(settings["face_ids"], ["14", "999"])
        self.assertEqual(settings["qzone_permission"], "friends")
        self.assertEqual(settings["group_trigger"], "all")
        self.assertFalse(settings["memory_enabled"])

    def test_whitelist(self) -> None:
        api, service = self.make_service()
        self.assertTrue(service.is_allowed("group:123456"))
        self.assertTrue(service.is_allowed("private:10001"))
        self.assertFalse(service.is_allowed("private:22222"))
        result = service._allow_target("private", "22222", add=True)
        self.assertIn("已加入", result)
        self.assertTrue(service.is_allowed("private:22222"))
        self.assertEqual(api.saved[-1]["allow_private"][-1], "22222")
        service._allow_target("private", "22222", add=False)
        self.assertFalse(service.is_allowed("private:22222"))

    def test_allow_target_parsing(self) -> None:
        self.assertEqual(_parse_allow_target("group:123"), ("group", "123"))
        self.assertEqual(_parse_allow_target("群:456"), ("group", "456"))
        self.assertEqual(_parse_allow_target("private:789"), ("private", "789"))
        self.assertEqual(_parse_allow_target("321"), ("private", "321"))
        self.assertEqual(_parse_allow_target("abc"), ("invalid", ""))

    def test_reply_flow(self) -> None:
        api, service = self.make_service()
        service._on_event_main(group_event("在吗"))
        self.assertTrue(service.client.event.wait(2))
        conversation, segments = service.client.sent[-1]
        self.assertEqual(conversation, "group:123456")
        self.assertEqual(segments[0]["data"]["text"], "好的。")
        self.assertTrue(api.chat_calls)
        system = api.chat_calls[-1][0]["content"]
        self.assertIn("人格卡", system)
        self.assertEqual(service.store.recent("group:123456")[-1]["role"], "assistant")

    def test_ignores_unknown_and_duplicates(self) -> None:
        api, service = self.make_service()
        stranger = private_event("你好", user_id=55555)
        service._on_event_main(stranger)
        self.assertFalse(api.chat_calls)

        payload = group_event("在吗", message_id=42)
        service._on_event_main(payload)
        service._on_event_main(payload)
        self.assertEqual(len(api.chat_calls), 1)

    def test_owner_command(self) -> None:
        api, service = self.make_service()
        service._on_event_main(private_event("/allow add 22222"))
        self.assertTrue(service.client.event.wait(2))
        self.assertIn("22222", service.settings["allow_private"])
        self.assertEqual(api.saved[-1]["allow_private"][-1], "22222")
        self.assertIn("已加入白名单", service.client.sent[-1][1][0]["data"]["text"])

    def test_qzone(self) -> None:
        api, service = self.make_service({"qzone_enabled": False})
        service._on_event_main(private_event("/qzone 今天天气不错"))
        self.assertTrue(service.client.event.wait(2))
        self.assertIn("还没开启", service.client.sent[-1][1][0]["data"]["text"])

        service.settings["qzone_enabled"] = True
        service._on_event_main(private_event("/qzone 今天天气不错", message_id=3))
        deadline = time.time() + 2
        while time.time() < deadline and not service.client.qzone:
            time.sleep(0.02)
        self.assertTrue(service.client.qzone)
        self.assertEqual(service.client.qzone[-1]["content"], "今天天气不错")
        self.assertEqual(service.client.qzone[-1]["ugc_right"], 4)

    def test_compaction(self) -> None:
        api, service = self.make_service()
        for index in range(5):
            service.store.append_inbox("group:1", "user", f"消息{index}", name="小明")
        api.reply = '{"memories":["主人喜欢猫"],"slang":[{"term":"好耶","meaning":"赞同"}]}'
        service._maybe_compact(force=True)
        self.assertFalse(service._compacting)
        self.assertEqual(len(api.chat_calls), 1)
        self.assertIn("主人喜欢猫", [item["text"] for item in service.store.memories()])
        self.assertIn("好耶", service.store.slang_terms())
        self.assertEqual(len(service.store.inbox()), 3)

    def test_format_qzone(self) -> None:
        self.assertIn("今天的猫", _format_qzone([{"tid": "1", "content": "今天的猫"}]))
        self.assertIn("没有读到", _format_qzone(None))

    def test_apply_settings_and_snapshot(self) -> None:
        api, service = self.make_service({"http_port": 18766})
        service.started = False
        result = service.apply_settings({"owner_qq": "88888", "http_port": 18777})
        self.assertTrue(result["ok"])
        self.assertEqual(service.settings["owner_qq"], "88888")
        self.assertEqual(api.settings["owner_qq"], "88888")
        self.assertEqual(service.settings["http_port"], 18777)

        restarted = []
        service.started = True
        service._rebuild_link = lambda: restarted.append(True) or "已重启"
        result = service.apply_settings({"http_port": 18778})
        self.assertTrue(restarted)
        self.assertEqual(result["message"], "已重启")
        self.assertEqual(service.settings["http_port"], 18778)

        snapshot = service.snapshot()
        self.assertTrue(snapshot["started"])
        self.assertEqual(snapshot["owner_qq"], "88888")
        self.assertIn("18778", snapshot["event_url"])

    def test_ui_content_helpers(self) -> None:
        api, service = self.make_service()
        service.set_persona_text("# 新人格\n\n说话像猫。")
        self.assertIn("说话像猫", service.persona_text())
        with self.assertRaises(ValueError):
            service.set_persona_text("")

        service.add_memory_text("主人喜欢冰美式")
        service.add_memory_text("主人养了一只叫年糕的猫")
        self.assertEqual(len(service.memory_items()), 2)
        service.remove_memory_text("冰美式")
        self.assertEqual(len(service.memory_items()), 1)

        service.add_slang_text("yyds", "永远的神")
        self.assertEqual(service.slang_items()[0]["term"], "yyds")
        service.remove_slang_text("yyds")
        self.assertEqual(service.slang_items(), [])

        result = service.allow_target_text("group", "group: 999 888", True)
        self.assertIn("已加入", result)
        self.assertIn("999888", service.allow_items()["group"])
        service.allow_target_text("group", "999888", False)
        self.assertNotIn("999888", service.allow_items()["group"])
        self.assertIn("数字", service.allow_target_text("private", "abc", True))

    def test_ui_habits_and_compact_callback(self) -> None:
        api, service = self.make_service()
        service.store.observe("group:123456", "在吗", 1)
        rows = service.habit_rows()
        self.assertEqual(rows[0]["conversation"], "group:123456")
        self.assertEqual(rows[0]["count"], 1)

        messages = []
        service.compact_now(messages.append)
        self.assertTrue(messages)
        self.assertIn("太少", messages[-1])

    def test_ui_send_and_qzone_guards(self) -> None:
        api, service = self.make_service({"qzone_enabled": False})
        messages = []
        service.send_test("private:33333", "你好", lambda ok, text: messages.append((ok, text)))
        self.assertFalse(messages[-1][0])
        self.assertIn("白名单", messages[-1][1])

        qzone_messages = []
        service.qzone_action(
            "publish", "测试", lambda ok, text: qzone_messages.append((ok, text))
        )
        self.assertFalse(qzone_messages[-1][0])
        self.assertIn("打开", qzone_messages[-1][1])


if __name__ == "__main__":
    unittest.main()
