from __future__ import annotations

import json
import math
import sys
import tempfile
import types
import unittest
from unittest import mock
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "lfelkeea.deepseek_chan"
sys.path.insert(0, str(PLUGIN))

from deepseek_chan import lines  # noqa: E402
from deepseek_chan.clips import (  # noqa: E402
    HOLD_SUFFIX,
    action_for_host,
    build_clips,
    build_hold_clips,
    expression_clips,
    model_expression_files,
    model_motions,
    model_parameters,
    motion_clip,
    procedural_clips,
    smooth_clip,
)
from deepseek_chan.menu import GROUPS, build_menu  # noqa: E402
from deepseek_chan.model_source import (  # noqa: E402
    MODEL_NAME,
    discover_models,
    extract_archive,
    extract_zip,
    find_model_file,
    locate,
    watermark_switch,
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

    def test_motion_bezier_uses_last_point_as_end(self) -> None:
        """贝塞尔段是 [1, c1t, c1v, c2t, c2v, t, v]，终点在最后。"""
        motion = {
            "Version": 3,
            "Meta": {"Duration": 0.5, "Fps": 30, "Loop": False},
            "Curves": [
                {
                    "Target": "Parameter",
                    "Id": "Param41",
                    "Segments": [
                        0.0, 0.0,
                        1, 0.1, 0.0, 0.3, 0.5, 0.4, 1.0,
                        0, 0.5, 2.0,
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bezier.motion3.json"
            path.write_text(json.dumps(motion), encoding="utf-8")
            clip = motion_clip(path, "bezier")

        times = [key["t"] for key in clip["keys"]]
        values = [key["params"]["Param41"] for key in clip["keys"]]
        self.assertEqual(times, sorted(times))
        self.assertAlmostEqual(times[-1], 0.5, places=3)
        self.assertAlmostEqual(values[-1], 2.0, places=3)
        # 控制点被误当成终点时，曲线会在 0.1~0.4 之间来回跳。
        for before, after in zip(values, values[1:]):
            self.assertGreaterEqual(after + 1e-6, before)

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

    def test_actions_drive_custom_input_params_when_present(self) -> None:
        available = {
            "ParamBodyAngleX",
            "ParamBodyAngleXinput",
            "ParamBodyAngleZ",
            "ParamBodyAngleZ2input",
            "ParamAngleX",
            "Param32x",
            "Param36",
        }
        clips = procedural_clips(available)

        hop = clips["hop"]
        for key in hop["keys"]:
            params = key["params"]
            if "ParamBodyAngleX" in params:
                self.assertAlmostEqual(
                    params["ParamBodyAngleXinput"], params["ParamBodyAngleX"]
                )

        dance = clips["dance"]
        for key in dance["keys"]:
            params = key["params"]
            if "ParamBodyAngleZ" in params:
                self.assertAlmostEqual(
                    params["ParamBodyAngleZ2input"], params["ParamBodyAngleZ"]
                )

        # 点头用标准头部俯仰参数，不能把只存在自定义模型里的 Param32x 当成俯仰。
        nod = clips["nod"]
        self.assertTrue(
            any(abs(key["params"].get("ParamAngleX", 0.0)) >= 9.0 for key in nod["keys"])
        )
        for key in nod["keys"]:
            self.assertNotIn("Param32x", key["params"])

    def test_wave_clips_use_raised_hand_without_prop(self) -> None:
        """挥手/伸懒腰/跳舞要举空手，不能切成狗尾草手型。"""
        clips = procedural_clips()
        for name in ("wave", "greet", "wave_big", "stretch", "dance"):
            params: set[str] = set()
            for key in clips[name]["keys"]:
                params.update(key["params"])
            self.assertNotIn("Movement1", params, name)
            self.assertIn("Movement2", params, name)
            self.assertEqual(
                clips[name]["snap"], ["Movement1", "Movement2", "Movement3"], name
            )

    def test_switch_params_do_not_blend(self) -> None:
        clip = smooth_clip(
            {
                "duration": 1.0,
                "loop": False,
                "keys": [
                    {"t": 0.0, "params": {"Movement2": 0.0}},
                    {"t": 1.0, "params": {"Movement2": 1.0}},
                ],
            }
        )
        values = {key["params"]["Movement2"] for key in clip["keys"]}
        self.assertEqual(values, {0.0, 1.0})

    def test_posture_clips_end_in_changed_state(self) -> None:
        """换装/变小/藏尾巴要停在改变后的状态，否则看起来像没换。"""
        clips = procedural_clips()
        last = {name: clips[name]["keys"][-1]["params"] for name in
                ("dress", "dress_short", "chibi", "chibi_normal", "tail_hide", "tail_show")}
        self.assertEqual(last["dress"]["Clothes1"], -1)
        self.assertEqual(last["dress_short"]["Clothes1"], 1)
        self.assertEqual(last["chibi"]["Clothes2"], 1)
        self.assertEqual(last["chibi_normal"]["Clothes2"], 0)
        self.assertEqual(last["tail_hide"]["Clothes5"], -1)
        self.assertEqual(last["tail_show"]["Clothes5"], 1)

    def test_hold_variants_keep_last_frame(self) -> None:
        clips = procedural_clips()
        held = build_hold_clips(clips)
        for name in ("dress", "dress_short", "chibi", "chibi_normal", "tail_hide", "tail_show"):
            variant = held[name + HOLD_SUFFIX]
            self.assertFalse(variant["loop"], name)
            self.assertGreater(variant["duration"], clips[name]["duration"] + 60, name)
            self.assertEqual(
                variant["keys"][-1]["params"], clips[name]["keys"][-1]["params"], name
            )
        # 循环动作没有末帧，不该生成保持变体。
        self.assertNotIn("idle_motion" + HOLD_SUFFIX, held)

    def test_idle_auto_play_only_uses_reversible_poses(self) -> None:
        from deepseek_chan import service

        auto = set(service.IDLE_GESTURES) | set(service.CUTE_GESTURES) | set(
            service.POSE_GESTURES
        )
        for name in ("dress", "chibi", "tail_hide", "tail_show", "special", "idle_motion"):
            self.assertNotIn(name, auto, name)
        for name in ("hand_up", "hand_down", "foxtail_out", "foxtail_in",
                     "point_out", "point_in"):
            self.assertIn(name, service.POSE_GESTURES, name)

    def test_static_rig_hides_actions_it_cannot_perform(self) -> None:
        available = {
            "ParamAngleX",
            "ParamAngleY",
            "ParamAngleZ",
            "Param32x",
            "Paramheadxy",
            "Param_Angle_Rotation1_ArtMesh1",
            "ParamEyeLOpen",
            "ParamEyeROpen",
            "ParamEyeBallX",
            "ParamEyeBallY",
            "ParamBrowLY",
            "ParamBrowRY",
            "ParamMouthOpenY",
            "ParamMouthForm",
            "CheeckPuff",
        }
        clips = procedural_clips(available)
        for name in ("nod", "hop", "wave", "greet", "stretch", "dance", "bow"):
            self.assertNotIn(name, clips, name)
        for name in ("shake", "tilt", "glance", "think", "surprise", "sneeze"):
            self.assertIn(name, clips, name)

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


class WatermarkTests(unittest.TestCase):
    def test_reads_author_switch_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "shuiyin1.exp3.json").write_text(
                json.dumps({"Parameters": [{"Id": "Paramheadxy", "Value": 30}]}),
                encoding="utf-8",
            )
            (directory / "水印2.exp3.json").write_text(
                json.dumps({"Parameters": [{"Id": "Paramheadxy3", "Value": 30}]}),
                encoding="utf-8",
            )
            (directory / "red.exp3.json").write_text(
                json.dumps({"Parameters": [{"Id": "ParamEyeLOpen", "Value": 0}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                watermark_switch(directory),
                {"Paramheadxy": 30.0, "Paramheadxy3": 30.0},
            )

    def test_missing_directory_is_safe(self) -> None:
        self.assertEqual(watermark_switch(None), {})
        self.assertEqual(watermark_switch(Path("definitely-not-here")), {})


class MenuTests(unittest.TestCase):
    def test_build_menu_uses_only_available_clips(self) -> None:
        played: list[str] = []
        expressed: list[str] = []
        cleared: list[bool] = []
        reset: list[bool] = []

        clips = {"wave": {}, "nod": {}, "dance": {}, "dress": {}}
        expressions = {"blush": {}, "heart": {}}
        groups = build_menu(
            clips,
            expressions,
            played.append,
            expressed.append,
            lambda: cleared.append(True),
            lambda: reset.append(True),
        )

        titles = [group["title"] for group in groups]
        self.assertEqual(titles, ["常用", "表演", "换装与小道具", "表情", "还原"])
        labels = [item["label"] for group in groups for item in group["items"]]
        self.assertIn("挥手", labels)
        self.assertIn("跳舞", labels)
        self.assertIn("换长裙", labels)
        self.assertIn("还原默认动作", labels)
        self.assertIn("还原默认表情", labels)
        self.assertNotIn("恢复默认表情", labels)
        self.assertNotIn("比心", labels)

        first = groups[0]["items"][0]
        first["callback"]()
        self.assertEqual(played, ["wave"])
        groups[3]["items"][0]["callback"]()
        self.assertEqual(expressed, ["blush"])
        groups[-1]["items"][0]["callback"]()
        self.assertEqual(reset, [True])
        groups[-1]["items"][1]["callback"]()
        self.assertEqual(cleared, [True])

    def test_build_menu_hides_groups_without_clips(self) -> None:
        groups = build_menu(
            {"wave": {}}, {}, lambda _name: None, lambda _name: None, lambda: None, lambda: None
        )
        self.assertEqual([group["title"] for group in groups], ["常用", "还原"])
        self.assertEqual([item["label"] for item in groups[0]["items"]], ["挥手"])

    def test_every_label_is_chinese_and_complete(self) -> None:
        from deepseek_chan.menu import CLIP_LABELS, EXPRESSION_LABELS

        self.assertEqual(set(CLIP_LABELS), {name for _, names in GROUPS for name in names})
        for label in list(CLIP_LABELS.values()) + list(EXPRESSION_LABELS.values()):
            self.assertTrue(label.strip())
            self.assertTrue(any(ord(char) > 127 for char in label), label)


class ServicePlayTests(unittest.TestCase):
    def _service(self):
        from deepseek_chan.service import DeepSeekChanService

        service = object.__new__(DeepSeekChanService)
        service.ready = True
        service.clips = {
            "hand_up": {"duration": 1.0, "loop": False, "keys": []},
            "idle_motion": {"duration": 5.0, "loop": True, "keys": []},
        }
        service.hold_clips = {
            "hand_up" + HOLD_SUFFIX: {"duration": 90000.0, "loop": False, "keys": []}
        }
        service._last_action = ""
        service._played_clips = set()
        calls = []

        class Model:
            def play(self, name, **kwargs):
                calls.append((name, kwargs))

            def stop(self, name):
                calls.append(("stop:" + name, {}))

        service.api = types.SimpleNamespace(
            model=Model(),
            log=lambda *args, **kwargs: None,
            action=lambda *args, **kwargs: None,
        )
        return service, calls

    def test_menu_play_uses_hold_variant(self) -> None:
        service, calls = self._service()
        service._play_menu("hand_up")
        self.assertEqual(calls[0][0], "hand_up" + HOLD_SUFFIX)
        self.assertFalse(calls[0][1]["loop"])

    def test_idle_play_does_not_hold(self) -> None:
        service, calls = self._service()
        service._play("hand_up")
        self.assertEqual(calls[0][0], "hand_up")
        self.assertFalse(calls[0][1]["loop"])

    def test_looping_idle_motion_stays_looping(self) -> None:
        service, calls = self._service()
        service._play_menu("idle_motion")
        self.assertEqual(calls[0][0], "idle_motion")
        self.assertTrue(calls[0][1]["loop"])

    def test_reset_action_stops_played_and_held_clips_only(self) -> None:
        service, calls = self._service()

        class Expressions:
            def __init__(self):
                self.cleared = 0

            def clear_expression(self):
                self.cleared += 1

        expressions = Expressions()
        service.api.model.clear_expression = expressions.clear_expression
        service._play_menu("hand_up")
        service._reset_action()
        stopped = [name for name, _ in calls if name.startswith("stop:")]
        self.assertIn("stop:hand_up" + HOLD_SUFFIX, stopped)
        # 还原动作不能顺手把表情也清掉。
        self.assertEqual(expressions.cleared, 0)
        self.assertEqual(service._played_clips, set())


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


class MenuInstallTests(unittest.TestCase):
    def _service(self, model):
        from types import SimpleNamespace

        from deepseek_chan.service import DeepSeekChanService

        logs: list[str] = []
        api = SimpleNamespace(
            manager=SimpleNamespace(pet=object(), data_dir=tempfile.gettempdir()),
            settings={},
            storage={},
            log=lambda text, *_args, **_kwargs: logs.append(str(text)),
            say=lambda *_args, **_kwargs: None,
            model=model,
        )
        return DeepSeekChanService(api), logs

    def test_installs_groups_on_new_host(self) -> None:
        captured: list[dict] = []
        model = type("Model", (), {
            "set_action_menu": lambda _self, groups: captured.extend(groups),
            "clear_expression": lambda _self: None,
        })()
        service, _logs = self._service(model)
        service.ready = True
        service.clips = {"wave": {}, "nod": {}, "dance": {}}
        service.expressions = {"blush": {}}

        service._install_menu()

        self.assertEqual(
            [group["title"] for group in captured], ["常用", "表演", "表情", "还原"]
        )
        labels = [item["label"] for group in captured for item in group["items"]]
        self.assertIn("挥手", labels)
        self.assertIn("还原默认表情", labels)

    def test_old_host_without_menu_api_only_logs(self) -> None:
        model = type("Model", (), {"clear_expression": lambda _self: None})()
        service, logs = self._service(model)
        service.ready = True
        service.clips = {"wave": {}}
        service._install_menu()
        self.assertTrue(any("1.4.1" in text for text in logs), logs)


class HostActionTests(unittest.TestCase):
    def _service(self):
        from types import SimpleNamespace

        from deepseek_chan.service import DeepSeekChanService

        played: list[str] = []
        actions: list[str] = []

        class Model:
            def play(self, name, **_kwargs):
                played.append(name)

            def set_action_menu(self, _groups):
                return None

        api = SimpleNamespace(
            manager=SimpleNamespace(pet=object(), data_dir=tempfile.gettempdir()),
            settings={},
            storage={},
            log=lambda *_args, **_kwargs: None,
            model=Model(),
            action=lambda name: actions.append(name),
        )
        service = DeepSeekChanService(api)
        service.ready = True
        service.clips = {"hop": {}, "dance": {}, "nod": {}}
        return service, played, actions

    def test_hop_and_dance_also_play_host_body_animation(self) -> None:
        service, played, actions = self._service()
        service._play("hop")
        self.assertEqual(played, ["hop"])
        self.assertEqual(actions, ["hop"])
        service._play("dance")
        self.assertEqual(actions, ["hop", "spin"])
        service._play("nod")
        self.assertEqual(actions, ["hop", "spin"])

    def test_host_sync_does_not_restart_host_animation(self) -> None:
        service, played, actions = self._service()
        service._play("hop", with_host=False)
        self.assertEqual(played, ["hop"])
        self.assertEqual(actions, [])


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


GENERIC_PARAMS = [
    "ParamAngleX",
    "ParamAngleY",
    "ParamAngleZ",
    "ParamBodyAngleX",
    "ParamBodyAngleZ",
    "ParamEyeLOpen",
    "ParamEyeROpen",
    "ParamEyeBallX",
    "ParamEyeBallY",
    "ParamBrowLY",
    "ParamBrowRY",
    "ParamMouthOpenY",
    "ParamMouthForm",
    "ParamBreath",
]

GENERIC_MOTION = {
    "Version": 3,
    "Meta": {"Duration": 2.0, "Fps": 30, "Loop": True},
    "Curves": [
        {"Target": "Parameter", "Id": "ParamAngleX", "Segments": [0.0, 0.0, 0, 1.0, 8.0, 0, 2.0, 0.0]},
        {"Target": "Parameter", "Id": "ParamEyeLOpen", "Segments": [0.0, 1.0, 0, 2.0, 1.0]},
    ],
}

GENERIC_EXPRESSION = {
    "Type": "Live2D Expression",
    "Parameters": [
        {"Id": "ParamEyeLOpen", "Value": 0.2},
        {"Id": "NotARealParam", "Value": 9.0},
    ],
}


def _write_generic_model(root: Path, *, with_tables: bool = True) -> Path:
    """一个没有大肥鱼特征参数的普通模型。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "模型.moc3").write_bytes(b"moc3")
    (root / "tex.png").write_bytes(b"png")
    (root / "模型.cdi3.json").write_text(
        json.dumps({"Parameters": [{"Id": name} for name in GENERIC_PARAMS + ["ParamExtra"]]}),
        encoding="utf-8",
    )
    (root / "motions").mkdir()
    (root / "motions" / "idle.motion3.json").write_text(
        json.dumps(GENERIC_MOTION), encoding="utf-8"
    )
    (root / "motions" / "tap.motion3.json").write_text(
        json.dumps(GENERIC_MOTION), encoding="utf-8"
    )
    (root / "expressions").mkdir()
    (root / "expressions" / "wink.exp3.json").write_text(
        json.dumps(GENERIC_EXPRESSION), encoding="utf-8"
    )
    data = {
        "Version": 3,
        "Groups": [
            {"Target": "Parameter", "Name": "Param", "Ids": ["ParamAngleX", "ParamEyeLOpen"]}
        ],
        "FileReferences": {"DisplayInfo": "模型.cdi3.json"},
    }
    if with_tables:
        data["FileReferences"]["Motions"] = {
            "Idle": [{"File": "motions/idle.motion3.json"}],
            "Tap": [{"File": "motions/tap.motion3.json"}],
        }
        data["FileReferences"]["Expressions"] = [
            {"Name": "wink", "File": "expressions/wink.exp3.json"}
        ]
    model = root / "模型.model3.json"
    model.write_text(json.dumps(data), encoding="utf-8")
    return model


class GenericModelTests(unittest.TestCase):
    def test_parameters_come_from_groups_and_display_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            _write_generic_model(root)
            params = model_parameters(root)
            self.assertIsNotNone(params)
            self.assertIn("ParamAngleX", params)
            self.assertIn("ParamExtra", params)
            self.assertNotIn("Param36", params)

    def test_signature_actions_are_dropped_for_other_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            _write_generic_model(root)
            clips, _expressions = build_clips(root)

            self.assertIn("nod", clips)
            self.assertNotIn("hop", clips)  # 没有手臂/躯干通道，做不出跳跃
            self.assertNotIn("wave", clips)  # 依赖 Param36
            self.assertNotIn("dress", clips)  # 依赖 Clothes1
            self.assertNotIn("shy", clips)  # 依赖 key7
            self.assertNotIn("pout", clips)  # 依赖 CheeckPuff

            for name, clip in clips.items():
                self.assertTrue(clip["keys"], name)
                for key in clip["keys"]:
                    for pid in key["params"]:
                        self.assertIn(pid, GENERIC_PARAMS + ["ParamExtra"], f"{name}:{pid}")

    def test_model_tables_supply_motions_and_expressions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            _write_generic_model(root)

            motions = dict(model_motions(root))
            self.assertIn("idle_motion", motions)
            self.assertTrue(motions["idle_motion"].name.endswith("idle.motion3.json"))

            expressions = dict(model_expression_files(root))
            self.assertIn("wink", expressions)

            clips, clips_expressions = build_clips(root)
            idle = clips["idle_motion"]
            self.assertTrue(idle["loop"])
            self.assertAlmostEqual(idle["duration"], 2.0)
            # 模型导出的过渡动作 Meta.Loop 是 true，但只应播一次，
            # 否则会反复伸手再缩回去。
            self.assertFalse(clips["tap"]["loop"])
            self.assertIn("wink", clips_expressions)
            params = clips_expressions["wink"]["keys"][0]["params"]
            self.assertEqual(set(params), {"ParamEyeLOpen"})
            self.assertNotIn("NotARealParam", params)

    def test_fallback_scan_keeps_known_expression_aliases_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            _write_generic_model(root, with_tables=False)
            (root / "动画").mkdir()
            (root / "动画" / "idle.motion3.json").write_text(
                json.dumps(GENERIC_MOTION), encoding="utf-8"
            )
            (root / "Exp3").mkdir()
            (root / "Exp3" / "脸红.exp3.json").write_text(
                json.dumps(GENERIC_EXPRESSION), encoding="utf-8"
            )
            (root / "Exp3" / "wink.exp3.json").write_text(
                json.dumps(GENERIC_EXPRESSION), encoding="utf-8"
            )

            motions = dict(model_motions(root))
            self.assertIn("idle_motion", motions)
            expressions = dict(model_expression_files(root))
            self.assertIn("blush", expressions)
            self.assertNotIn("wink", expressions)

    def test_all_generated_clips_are_dense(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            _write_generic_model(root)
            clips, _ = build_clips(root)
            for name, clip in clips.items():
                keys = clip["keys"]
                self.assertGreaterEqual(len(keys), 2, name)
                for before, after in zip(keys, keys[1:]):
                    gap = after["t"] - before["t"]
                    self.assertLessEqual(gap, 0.018, f"{name}: {gap}")


class SmoothClipTests(unittest.TestCase):
    def test_resamples_sparse_keys_at_60hz(self) -> None:
        clip = {
            "duration": 1.0,
            "loop": False,
            "keys": [
                {"t": 0.0, "params": {"ParamA": 0.0}},
                {"t": 0.5, "params": {"ParamA": 1.0}},
                {"t": 1.0, "params": {"ParamA": 0.0}},
            ],
        }
        smooth = smooth_clip(clip, rate=60.0)
        self.assertAlmostEqual(smooth["duration"], 1.0)
        self.assertEqual(len(smooth["keys"]), 61)
        for before, after in zip(smooth["keys"], smooth["keys"][1:]):
            self.assertAlmostEqual(after["t"] - before["t"], 1 / 60, delta=0.001)

        # smoothstep 会在中点给出 0.5；Hermite 应该带着速度穿过去。
        middle = min(smooth["keys"], key=lambda key: abs(key["t"] - 0.25))
        self.assertGreater(middle["params"]["ParamA"], 0.55)

    def test_loop_seam_has_matching_slope(self) -> None:
        clip = {
            "duration": 1.0,
            "loop": True,
            "keys": [
                {"t": 0.0, "params": {"ParamA": 0.0}},
                {"t": 0.5, "params": {"ParamA": 1.0}},
                {"t": 1.0, "params": {"ParamA": 0.0}},
            ],
        }
        smooth = smooth_clip(clip, rate=120.0)
        keys = smooth["keys"]
        start = keys[1]["params"]["ParamA"] - keys[0]["params"]["ParamA"]
        end = keys[-1]["params"]["ParamA"] - keys[-2]["params"]["ParamA"]
        self.assertGreater(start, 0)
        self.assertLess(end, 0)
        # 接缝两侧速度方向一致、幅度接近，循环时不会卡顿。
        self.assertAlmostEqual(abs(start), abs(end), delta=abs(start) * 0.35)

    def test_procedural_clips_are_smoothed(self) -> None:
        clips = procedural_clips()
        for name, clip in clips.items():
            for before, after in zip(clip["keys"], clip["keys"][1:]):
                gap = after["t"] - before["t"]
                self.assertLessEqual(gap, 0.018, f"{name}: {gap}")


class GenericArchiveTests(unittest.TestCase):
    def test_extract_any_zipped_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "任意模型.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("任意模型/任意.model3.json", "{}")
                handle.writestr("任意模型/模型.moc3", b"moc3")
                handle.writestr("任意模型/tex.png", b"png")
                handle.writestr("任意模型/psd/模型.psd", b"psd")
            model = extract_archive(archive, tmp_path / "data")
            self.assertEqual(model.name, "任意.model3.json")
            self.assertIn("model_", model.parent.name)
            self.assertTrue((model.parent / "模型.moc3").is_file())
            self.assertFalse((model.parent / "psd").exists())
            self.assertTrue((model.parent / ".source.json").is_file())

    def test_scan_finds_rar_and_7z_archives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "免费模型冰糖.rar").write_bytes(b"rar-bytes")
            (root / "免费模型艾莲.7z").write_bytes(b"7z-bytes")

            with mock.patch(
                "deepseek_chan.model_source._legacy_candidates", return_value=[]
            ):
                location = locate({"search_dirs": [str(root)]})
                models = discover_models({"search_dirs": [str(root)]})

            self.assertIsNotNone(location)
            self.assertIsNotNone(location.archive)
            self.assertIn(location.archive.name, {"免费模型冰糖.rar", "免费模型艾莲.7z"})
            archives = {
                item.archive.name for item in models if item.archive is not None
            }
            self.assertEqual(archives, {"免费模型冰糖.rar", "免费模型艾莲.7z"})

    def test_manual_model_folder_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型"
            model = _write_generic_model(root)
            with mock.patch(
                "deepseek_chan.model_source._legacy_candidates", return_value=[]
            ):
                location = locate({"model_path": str(root)})
                models = discover_models({"model_path": str(root), "search_dirs": []})
            self.assertIsNotNone(location)
            self.assertEqual(location.model, model)
            self.assertEqual(location.source, "手动指定")
            self.assertEqual(models[0].model, model)


class GenericMenuTests(unittest.TestCase):
    def test_menu_keeps_model_specific_actions_and_expressions(self) -> None:
        played: list[str] = []
        expressed: list[str] = []
        groups = build_menu(
            {"nod": {}, "idle_motion": {}, "special_tap": {}},
            {"blush": {}, "wink": {}},
            played.append,
            expressed.append,
            lambda: None,
            lambda: None,
        )
        titles = [group["title"] for group in groups]
        self.assertIn("常用", titles)
        self.assertIn("模型自带", titles)
        self.assertIn("表情", titles)

        labels = [item["label"] for group in groups for item in group["items"]]
        self.assertIn("点头", labels)
        self.assertIn("待机动作", labels)
        self.assertIn("special_tap", labels)
        self.assertIn("脸红", labels)
        self.assertIn("wink", labels)
        self.assertIn("还原默认表情", labels)

        extra = next(group for group in groups if group["title"] == "模型自带")
        extra["items"][0]["callback"]()
        self.assertEqual(played, ["special_tap"])


class SettingsMigrationTests(unittest.TestCase):
    def test_old_30fps_is_bumped_once(self) -> None:
        from types import SimpleNamespace

        from deepseek_chan.service import DeepSeekChanService

        saved: list[dict] = []
        api = SimpleNamespace(
            manager=SimpleNamespace(pet=object(), data_dir=tempfile.gettempdir()),
            settings={"fps": 30},
            storage={},
            save_settings=lambda values: saved.append(dict(values)),
        )
        service = DeepSeekChanService(api)
        service._migrate_settings()
        self.assertEqual(saved, [{"settings_version": 2, "fps": 60}])
        self.assertEqual(service.settings["fps"], 60)

        service._migrate_settings()
        self.assertEqual(len(saved), 1)

    def test_manual_30fps_is_kept_after_migration(self) -> None:
        from types import SimpleNamespace

        from deepseek_chan.service import DeepSeekChanService

        saved: list[dict] = []
        api = SimpleNamespace(
            manager=SimpleNamespace(pet=object(), data_dir=tempfile.gettempdir()),
            settings={"settings_version": 2, "fps": 30},
            storage={},
            save_settings=lambda values: saved.append(dict(values)),
        )
        service = DeepSeekChanService(api)
        service._migrate_settings()
        self.assertEqual(saved, [])
        self.assertEqual(service.settings["fps"], 30)


if __name__ == "__main__":
    unittest.main()
