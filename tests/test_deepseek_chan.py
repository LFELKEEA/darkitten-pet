from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.deepseek_chan"
sys.path.insert(0, str(PLUGIN))

from deepseek_chan import lines  # noqa: E402
from deepseek_chan.clips import (  # noqa: E402
    action_for_host,
    build_clips,
    expression_clips,
    motion_clip,
    procedural_clips,
)
from deepseek_chan.menu import GROUPS, build_menu  # noqa: E402
from deepseek_chan.model_source import (  # noqa: E402
    MODEL_NAME,
    extract_zip,
    find_model_file,
    locate,
)

REAL_MODEL_DIR = ROOT / "data" / "models" / "deepseek_chan"

MOTION = {
    "Version": 3,
    "Meta": {"Duration": 1.0, "Fps": 30, "Loop": False},
    "Curves": [
        {
            "Target": "Parameter",
            "Id": "ParamAngleX",
            "Segments": [0.0, 0.0, 1.0, 10.0],
        },
        {
            "Target": "Parameter",
            "Id": "ParamMouthForm",
            "Segments": [0.0, -1.0, 0.0, 1.0, 0.5, 0.0, 1.0, 0.8],
        },
        {"Target": "Model", "Id": "Opacity", "Segments": [0.0, 1.0, 1.0, 1.0]},
    ],
}

EXPRESSION = {
    "Type": "Live2D Expression",
    "Parameters": [
        {"Id": "key1", "Value": 1.0, "Blend": "Add"},
        {"Id": "ParamEyeLOpen", "Value": 0.4},
        {"Id": "Unknown", "Value": 9.0},
    ],
}


def _write_model_zip(path: Path, *, model_name: str = MODEL_NAME) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"公皮/{model_name}", json.dumps({"Version": 3}))
        archive.writestr("公皮/大肥鱼.moc3", b"moc3-bytes")
        archive.writestr("公皮/大肥鱼.4096/texture_00.png", b"png-bytes")
        archive.writestr("公皮/Animation/idle.motion3.json", json.dumps(MOTION))
        archive.writestr("公皮/Exp3/脸红.exp3.json", json.dumps(EXPRESSION))
        archive.writestr("公皮/psd/大肥鱼.psd", b"psd-bytes")
        archive.writestr("公皮/preview.jpg", b"jpg-bytes")
        # 目录穿越：即使后缀在白名单里也不能写出去。
        archive.writestr("公皮/../evil.txt", b"evil")
        archive.writestr("公皮/sub/../../evil2.txt", b"evil")


class ModelSourceTests(unittest.TestCase):
    def test_extract_only_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "公皮.zip"
            _write_model_zip(archive)
            data_dir = tmp_path / "data"

            model = extract_zip(archive, data_dir)
            self.assertEqual(model.name, MODEL_NAME)
            folder = model.parent
            self.assertTrue((folder / "大肥鱼.moc3").is_file())
            self.assertTrue((folder / "大肥鱼.4096" / "texture_00.png").is_file())
            self.assertTrue((folder / "Animation" / "idle.motion3.json").is_file())
            self.assertTrue((folder / "Exp3" / "脸红.exp3.json").is_file())
            self.assertFalse((folder / "psd").exists())
            self.assertFalse((folder / "preview.jpg").exists())
            self.assertFalse((tmp_path / "evil.txt").exists())
            self.assertFalse((tmp_path / "evil2.txt").exists())
            self.assertTrue((folder / ".source.json").is_file())

    def test_extract_reuses_previous_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "公皮.zip"
            _write_model_zip(archive)
            data_dir = tmp_path / "data"

            first = extract_zip(archive, data_dir)
            stamp = first.stat().st_mtime_ns
            second = extract_zip(archive, data_dir)
            self.assertEqual(first, second)
            self.assertEqual(stamp, second.stat().st_mtime_ns)

    def test_extract_without_model_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "empty.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("readme.txt", "nothing here")
            with self.assertRaises(ValueError):
                extract_zip(archive, tmp_path / "data")

    def test_locate_prefers_manual_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "公皮.zip"
            _write_model_zip(archive)
            model = extract_zip(archive, tmp_path / "data")

            by_model = locate({"model_path": str(model)})
            self.assertIsNotNone(by_model)
            self.assertTrue(by_model.ready)

            by_zip = locate({"zip_path": str(archive)})
            self.assertIsNotNone(by_zip)
            self.assertFalse(by_zip.ready)
            self.assertEqual(by_zip.zip_path, archive)

            by_dir = locate({"model_path": str(model.parent)})
            self.assertIsNotNone(by_dir)
            self.assertEqual(by_dir.model, model)

    def test_find_model_file_prefers_canonical_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").mkdir()
            (root / "a" / "other.model3.json").write_text("{}", encoding="utf-8")
            (root / "b").mkdir()
            (root / "b" / MODEL_NAME).write_text("{}", encoding="utf-8")
            found = find_model_file(root)
            self.assertEqual(found.name, MODEL_NAME)
            self.assertIsNone(find_model_file(root / "missing"))


class ClipTests(unittest.TestCase):
    def test_procedural_clips_are_well_formed(self) -> None:
        clips = procedural_clips()
        self.assertGreaterEqual(len(clips), 30)
        for name in ("wave", "think", "pout", "shy", "love", "dress", "chibi", "hop"):
            self.assertIn(name, clips)
        for name, clip in clips.items():
            self.assertGreaterEqual(clip["duration"], 0.05, name)
            self.assertTrue(clip["keys"], name)
            previous = -1.0
            for key in clip["keys"]:
                self.assertGreaterEqual(key["t"], 0.0, name)
                self.assertGreaterEqual(key["t"], previous, name)
                previous = key["t"]
                for param, value in key["params"].items():
                    self.assertIsInstance(param, str)
                    self.assertTrue(math.isfinite(value), f"{name}:{param}")

    def test_motion_clip_parses_curves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.motion3.json"
            path.write_text(json.dumps(MOTION), encoding="utf-8")
            clip = motion_clip(path, "test")
            self.assertFalse(clip["loop"])
            self.assertAlmostEqual(clip["duration"], 1.0)
            params = set()
            for key in clip["keys"]:
                params.update(key["params"])
            self.assertEqual(params, {"ParamAngleX", "ParamMouthForm"})

    def test_expression_clip_filters_non_model_params(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "Exp3"
            folder.mkdir()
            (folder / "脸红.exp3.json").write_text(json.dumps(EXPRESSION), encoding="utf-8")
            (folder / "不认识.exp3.json").write_text(json.dumps(EXPRESSION), encoding="utf-8")
            expressions = expression_clips(root)
            self.assertEqual(set(expressions), {"blush"})
            clip = expressions["blush"]
            self.assertTrue(clip["loop"])
            params = clip["keys"][0]["params"]
            self.assertEqual(set(params), {"key1", "ParamEyeLOpen"})

    def test_host_actions_map_to_real_clips(self) -> None:
        clips = procedural_clips()
        for host in ("hop", "spin", "stretch", "shake", "lean", "nod"):
            target = action_for_host(host)
            self.assertIsNotNone(target, host)
            self.assertIn(target, clips, host)
        self.assertIsNone(action_for_host("unknown"))

    @unittest.skipUnless(
        (REAL_MODEL_DIR / MODEL_NAME).is_file(),
        "本机还没有解出大肥鱼模型，跳过真实动作表检查",
    )
    def test_real_model_builds_rich_actions(self) -> None:
        clips, expressions = build_clips(REAL_MODEL_DIR)
        self.assertGreaterEqual(len(clips), 30)
        self.assertEqual(len(expressions), 22)
        for name in (
            "wave",
            "wave_big",
            "think",
            "love",
            "dress",
            "chibi",
            "special",
            "foxtail_out",
            "point_out",
            "hand_up",
            "hand_down",
            "idle_motion",
            "hop",
        ):
            self.assertIn(name, clips, name)
        for name, clip in clips.items():
            self.assertTrue(clip["keys"], name)
            for key in clip["keys"]:
                for value in key["params"].values():
                    self.assertTrue(math.isfinite(value), name)
        self.assertIn("blush", expressions)
        self.assertIn("heart", expressions)
        self.assertIn("body_small", expressions)
        self.assertIn("tail_off", expressions)
        self.assertIn("point_on", expressions)


class MenuTests(unittest.TestCase):
    def test_build_menu_uses_only_available_clips(self) -> None:
        played: list[str] = []
        expressed: list[str] = []
        cleared: list[bool] = []

        clips = {"wave": {}, "nod": {}, "dance": {}, "dress": {}}
        expressions = {"blush": {}, "heart": {}}
        groups = build_menu(
            clips,
            expressions,
            played.append,
            expressed.append,
            lambda: cleared.append(True),
        )

        titles = [group["title"] for group in groups]
        self.assertEqual(titles, ["常用", "表演", "换装与小道具", "表情"])
        labels = [item["label"] for group in groups for item in group["items"]]
        self.assertIn("挥手", labels)
        self.assertIn("转圈跳舞", labels)
        self.assertIn("换长裙", labels)
        self.assertIn("恢复默认表情", labels)
        self.assertNotIn("比心", labels)

        first = groups[0]["items"][0]
        first["callback"]()
        self.assertEqual(played, ["wave"])
        groups[-1]["items"][0]["callback"]()
        self.assertEqual(expressed, ["blush"])
        groups[-1]["items"][-1]["callback"]()
        self.assertEqual(cleared, [True])

    def test_build_menu_hides_groups_without_clips(self) -> None:
        groups = build_menu({"wave": {}}, {}, lambda _name: None, lambda _name: None, lambda: None)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["title"], "常用")
        self.assertEqual([item["label"] for item in groups[0]["items"]], ["挥手"])

    def test_every_label_is_chinese_and_complete(self) -> None:
        from deepseek_chan.menu import CLIP_LABELS, EXPRESSION_LABELS

        self.assertEqual(set(CLIP_LABELS), {name for _, names in GROUPS for name in names})
        for label in list(CLIP_LABELS.values()) + list(EXPRESSION_LABELS.values()):
            self.assertTrue(label.strip())
            self.assertTrue(any(ord(char) > 127 for char in label), label)


class LinesTests(unittest.TestCase):
    def test_every_category_has_text(self) -> None:
        for kind, pool in lines.LINES.items():
            self.assertTrue(pool, kind)
            for text in pool:
                self.assertTrue(text.strip(), kind)
                self.assertLess(len(text), 160, kind)

    def test_all_kinds_used_by_service_exist(self) -> None:
        for kind in (
            "greet",
            "idle",
            "pat",
            "poke",
            "sleep",
            "wake",
            "drag",
            "chat_open",
            "chatter",
            "no_model",
            "credit",
            "error",
        ):
            self.assertTrue(lines.pick(kind), kind)
        for action in ("hop", "spin", "stretch", "shake", "lean", "nod"):
            self.assertTrue(lines.pick(f"action:{action}"), action)

    def test_greeting_buckets(self) -> None:
        cases = {0: "night", 4: "night", 6: "morning", 10: "morning",
                 12: "noon", 15: "afternoon", 20: "evening", 23: "night"}
        for hour, bucket in cases.items():
            text = lines.greeting_for_hour(hour)
            self.assertIn(text, lines.GREETINGS[bucket], hour)

    def test_no_model_guides_to_bilibili_author(self) -> None:
        for text in lines.LINES["no_model"]:
            self.assertIn("13737731", text)

    def test_persona_is_fan_made_and_not_official(self) -> None:
        prompt = lines.PERSONA_PROMPT
        self.assertIn("深度求索娘", prompt)
        self.assertIn("二创", prompt)
        self.assertIn("不是官方", prompt)
        self.assertIn("不编造", prompt)
        credit = " ".join(lines.LINES["credit"])
        self.assertIn("神宫凉子", credit)
        self.assertIn("霓虹猫_NeonNeko", credit)

    def test_reaction_line_classifies(self) -> None:
        self.assertIn(lines.reaction_line("你好可爱"), lines.LINES["praise"])
        self.assertIn(lines.reaction_line("你好笨"), lines.LINES["scold"])
        self.assertIn(lines.reaction_line("这个报错怎么办"), lines.LINES["question"])
        self.assertEqual(lines.reaction_line("今天天气不错"), "")


class OldHostTests(unittest.TestCase):
    """1.4.0 之前的桌宠没有 pet.model 接口，插件要给出可执行的提示。"""

    def test_missing_model_api_asks_for_upgrade(self) -> None:
        from types import SimpleNamespace

        from deepseek_chan.service import DeepSeekChanService

        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / MODEL_NAME
            model.write_text("{}", encoding="utf-8")
            api = SimpleNamespace(
                manager=SimpleNamespace(pet=object(), data_dir=tmp),
                settings={},
                storage={},
                said=[],
                log=lambda *_args, **_kwargs: None,
                say=lambda text, *_args, **_kwargs: api.said.append(text),
            )
            service = DeepSeekChanService(api)
            service._load(model)
            self.assertEqual(len(api.said), 1)
            self.assertIn("1.4.0", api.said[0])


class PackagingTests(unittest.TestCase):
    def test_plugin_ships_without_model_assets(self) -> None:
        banned = {".moc3", ".png", ".zip", ".psd", ".jpg", ".jpeg", ".wav", ".mp3"}
        offenders = [
            path.relative_to(PLUGIN)
            for path in PLUGIN.rglob("*")
            if path.is_file() and path.suffix.lower() in banned
        ]
        self.assertEqual(offenders, [])

    def test_manifest_declares_needed_permissions(self) -> None:
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["id"], "lfelkeea.deepseek_chan")
        self.assertEqual(manifest["api_version"], 1)
        self.assertTrue((PLUGIN / manifest["entry"]).is_file())
        permissions = set(manifest["permissions"])
        for permission in ("pet.model", "pet.persona", "pet.say", "pet.action",
                           "events", "timer", "settings", "storage", "chat.context"):
            self.assertIn(permission, permissions)
        settings = manifest["settings"]
        self.assertGreater(float(settings["height"]), 0)
        self.assertGreaterEqual(float(settings["idle_min_seconds"]), 1)

    def test_manifest_points_to_model_author_and_host_version(self) -> None:
        manifest = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))
        text = json.dumps(manifest, ensure_ascii=False)
        self.assertIn("13737731", text)
        self.assertIn("1.4.0", text)
        self.assertNotIn("DeepSeek免费公皮.zip", {
            path.name for path in PLUGIN.rglob("*") if path.is_file()
        })


if __name__ == "__main__":
    unittest.main()
