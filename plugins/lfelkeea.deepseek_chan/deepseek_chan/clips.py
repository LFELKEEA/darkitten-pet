"""动作与表情。

两来源：
1. 模型自带的 motion3.json（举手、竖手指、狗尾草、脱捕），转成参数关键帧；
2. 用模型实际可动的参数手写的动作，覆盖头、身体、手臂、手型、嘴、眼睛、服装。
"""

from __future__ import annotations

import bisect
import json
import re
from pathlib import Path

from .model_source import find_model_file

# motion3 文件名 → 动作名
MOTION_ALIASES = {
    "举手切默认手": "hand_down",
    "默认手切举手": "hand_up",
    "狗尾草切换出来": "foxtail_out",
    "狗尾草切回默认手": "foxtail_in",
    "竖手指切出来动画": "point_out",
    "竖手指切回默认": "point_in",
    "脱捕": "special",
    "IDLE": "idle_motion",
}

# exp3 文件名 → 表情名（脸部、小道具、体型和手型都能当常驻姿势）
EXPRESSION_ALIASES = {
    "黑脸": "dark",
    "流泪": "tears",
    "圈圈眼": "dizzy",
    "爱心眼": "heart",
    "＞＜": "squint",
    "屑屑眼": "smug",
    "脸红": "blush",
    "加载": "loading",
    "兔牙": "fang",
    "钢盆": "pot",
    "黑框眼镜": "glasses",
    "单片眼镜": "monocle",
    "流冷汗": "sweat",
    "默认体型": "body_default",
    "体型1": "body_small",
    "体型2": "body_large",
    "前倾": "lean",
    "胸口衣服": "chest_cloth",
    "脱鞋": "shoes_off",
    "尾巴开关": "tail_off",
    "竖食指开": "point_on",
    "竖食指关": "point_off",
}

# 这些参数对模型有效，表情文件里出现时要保留。
KEEP_PARAM_PREFIXES = ("key", "Param", "Clothes", "Movement", "LeanInput")

# 没有 cdi3 参数表时的兜底：只发标准 Live2D 参数，避免把大肥鱼的专用参数
# 塞进别的模型。
COMMON_PARAMS = {
    "ParamAngleX",
    "ParamAngleY",
    "ParamAngleZ",
    "ParamBodyAngleX",
    "ParamBodyAngleY",
    "ParamBodyAngleZ",
    "ParamEyeLOpen",
    "ParamEyeROpen",
    "ParamEyeLSmile",
    "ParamEyeRSmile",
    "ParamEyeBallX",
    "ParamEyeBallY",
    "ParamBrowLY",
    "ParamBrowRY",
    "ParamMouthOpenY",
    "ParamMouthForm",
    "ParamBreath",
}

# 模型自带动作的英文名提示。命中后给一个中文菜单名。
MOTION_HINTS = {
    "idle": ("idle_motion", "待机"),
    "idle2": ("idle_motion_2", "待机 2"),
    "idle3": ("idle_motion_3", "待机 3"),
    "tap": ("tap", "点一下"),
    "touch": ("touch", "碰一下"),
    "pat": ("pat", "摸摸头"),
    "greet": ("greet_motion", "打招呼"),
    "hello": ("hello", "你好"),
    "wave": ("wave_motion", "挥手"),
    "happy": ("happy", "开心"),
    "angry": ("angry", "生气"),
    "sad": ("sad", "难过"),
    "shy": ("shy_motion", "害羞"),
    "sleep": ("sleep_motion", "睡觉"),
    "wake": ("wake_motion", "醒来"),
}

SMOOTH_RATE = 60.0

# 有些作者把标准参数接在很弱的变形器上，真正驱动身体的是另一组
# 自定义参数（比如 xxxinput）。模型同时有这两套参数时，给同一个
# 动作补一份等价通道，动作才会真的动起来；目标参数不存在就跳过，
# 不会影响原来的大肥鱼模型。
PARAM_ALIASES: dict[str, tuple[str, ...]] = {
    "ParamBodyAngleX": ("ParamBodyAngleXinput",),
    "ParamBodyAngleY": ("ParamBodyAngleY3input",),
    "ParamBodyAngleZ": ("ParamBodyAngleZ2input",),
}

# 大肥鱼用这三组参数切换手型（默认手 / 狗尾草手 / 竖手指手）。
# 它们只能取 0 或 1，插值到中间值会把两只手同时画出来。
SNAP_PARAMS = ("Movement1", "Movement2", "Movement3")

# 挥手、鞠躬、跳舞这类动作需要手臂或躯干真的能弯。只有脸部/头部参数的
# 静态立绘模型做不出这些姿势，宁可不在菜单里出现，也别让「鞠躬」变成
# 原地晃一下。
ARTICULATED_PARAMS = (
    "Param36",
    "LArm",
    "RArm",
    "LForearm",
    "RForearm",
    "LHand",
    "RHand",
    "Movement1",
    "Movement2",
    "Movement3",
)
BODY_ACTIONS = {"hop", "wave", "greet", "wave_big", "stretch", "dance", "bow"}


def _can_articulate(available: set[str] | None) -> bool:
    if not available:
        return False
    return any(pid in available for pid in ARTICULATED_PARAMS)


def _is_static_rig(available: set[str] | None) -> bool:
    """没有可动手臂、但带大量自动命名旋转变形器的，基本是静态立绘。"""
    if not available or _can_articulate(available):
        return False
    return any(pid.startswith("Param_Angle_Rotation") for pid in available)

# 这些动作靠一个标志性参数成立；模型没有这个参数时，宁可不显示，
# 也不要把它变成只有头部晃动的「假动作」。
SIGNATURE_PARAMS = {
    "wave": "Param36",
    "greet": "Param36",
    "wave_big": "Param36",
    "stretch": "Param36",
    "dress": "Clothes1",
    "dress_short": "Clothes1",
    "chibi": "Clothes2",
    "chibi_normal": "Clothes2",
    "tail_hide": "Clothes5",
    "tail_show": "Clothes5",
    "shy": "key7",
    "love": "key4",
    "dizzy": "key3",
    "squint": "key5",
    "cry": "key2",
    "deadpan": "key1",
    "sweat": "key13",
    "pout": "CheeckPuff",
}


def _clip(duration: float, frames, loop: bool = False) -> dict:
    keys = []
    for at, params in frames:
        keys.append(
            {
                "t": round(max(0.0, float(at)), 3),
                "params": {str(key): round(float(value), 4) for key, value in params.items()},
            }
        )
    return {
        "duration": round(max(0.05, float(duration)), 3),
        "loop": bool(loop),
        "keys": keys,
        # 手型/道具是开关，不是连续角度。渲染层按中点吸附，避免淡入淡出时
        # 两只手叠在一起。
        "snap": list(SNAP_PARAMS),
    }


def _wave_frames(*, high: float, start: float = 0.18, end: float = 2.55) -> list:
    """抬手挥一挥。

    Param36 让手臂抬起来，Movement2 把手型切到「举起的手」。
    这里不能用 Movement1：那是作者做狗尾草道具用的手型，挥手时会凭空
    多出一根草，看着像动作坏了。
    """
    return [
        (0.0, {"Param36": 0, "Movement2": 0, "Movement3": 0, "AngleZ": 0, "AngleY": 0}),
        (start, {"Param36": high * 0.45, "Movement2": 1, "AngleZ": 4, "AngleY": -2,
                 "ParamMouthForm": 0.45, "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
        (start + 0.28, {"Param36": high, "Movement2": 1, "Movement3": 0, "AngleZ": 7,
                        "AngleY": -3, "ParamMouthForm": 0.6, "CheeckPuff": 0.15}),
        (start + 0.52, {"Param36": high * 0.86, "Movement2": 1, "AngleZ": 4,
                        "LForearm": 0.3, "LHand": 0.2}),
        (start + 0.76, {"Param36": high, "Movement2": 1, "AngleZ": 8, "LForearm": -0.25,
                        "LHand": -0.15}),
        (start + 1.0, {"Param36": high * 0.84, "Movement2": 1, "AngleZ": 3,
                       "LForearm": 0.32, "LHand": 0.2}),
        (start + 1.24, {"Param36": high, "Movement2": 1, "AngleZ": 8, "LForearm": -0.22,
                        "LHand": -0.12}),
        (end - 0.25, {"Param36": high * 0.4, "Movement2": 1, "AngleZ": 3,
                      "LForearm": 0, "LHand": 0, "ParamMouthForm": 0.35}),
        (end, {"Param36": 0, "Movement2": 0, "AngleZ": 0, "AngleY": 0,
               "ParamMouthForm": 0.15, "CheeckPuff": 0}),
    ]


def _model_config(model_dir: Path) -> tuple[Path | None, dict]:
    path = find_model_file(model_dir)
    if path is None:
        return None, {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return path, {}
    return path, data if isinstance(data, dict) else {}


def model_parameters(model_dir: Path) -> set[str] | None:
    """读取模型真正拥有的参数 ID。读不到时返回 None。"""
    model_json, data = _model_config(model_dir)
    base = model_json.parent if model_json is not None else Path(model_dir)
    ids: set[str] = set()

    for group in data.get("Groups") or []:
        if isinstance(group, dict) and group.get("Target") == "Parameter":
            ids.update(str(item) for item in group.get("Ids") or [])

    refs = data.get("FileReferences") or {}
    display = refs.get("DisplayInfo")
    cdi_paths: list[Path] = []
    if display:
        cdi_paths.append(base / str(display))
    try:
        cdi_paths.extend(sorted(base.rglob("*.cdi3.json")))
    except OSError:
        pass
    for cdi in cdi_paths:
        if not cdi.is_file():
            continue
        try:
            info = json.loads(cdi.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        for item in info.get("Parameters") or []:
            if isinstance(item, dict) and item.get("Id"):
                ids.add(str(item["Id"]))
        if ids:
            break

    return ids or None


def _resolve_ref(base: Path, relative: str) -> Path | None:
    if not relative:
        return None
    path = (base / str(relative)).resolve()
    try:
        path.relative_to(base.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _unique_key(name: str, used: set[str]) -> str:
    key = str(name or "motion")
    if key not in used:
        used.add(key)
        return key
    index = 2
    while f"{key}_{index}" in used:
        index += 1
    key = f"{key}_{index}"
    used.add(key)
    return key


def model_motions(model_dir: Path) -> list[tuple[str, Path]]:
    """返回 [(动作名, motion3 文件)]，优先用 model3.json 的 Motions 表。"""
    model_json, data = _model_config(model_dir)
    base = model_json.parent if model_json is not None else Path(model_dir)
    entries: list[tuple[str, Path]] = []
    refs = data.get("FileReferences") or {}
    used: set[str] = set()

    motions = refs.get("Motions")
    if isinstance(motions, dict):
        for group in motions.values():
            if not isinstance(group, list):
                continue
            for item in group:
                if not isinstance(item, dict):
                    continue
                path = _resolve_ref(base, str(item.get("File") or ""))
                if path is None:
                    continue
                stem = path.name[: -len(".motion3.json")]
                alias = MOTION_ALIASES.get(stem)
                if alias is None:
                    hint = MOTION_HINTS.get(stem.lower())
                    alias = hint[0] if hint else stem
                entries.append((_unique_key(alias, used), path))

    if not entries:
        try:
            files = sorted(base.rglob("*.motion3.json"))
        except OSError:
            files = []
        for path in files:
            stem = path.name[: -len(".motion3.json")]
            alias = MOTION_ALIASES.get(stem)
            if alias is None:
                hint = MOTION_HINTS.get(stem.lower())
                alias = hint[0] if hint else stem
            entries.append((_unique_key(alias, used), path))
    return entries


def model_expression_files(model_dir: Path) -> list[tuple[str, Path]]:
    """返回 [(表情名, exp3 文件)]。"""
    model_json, data = _model_config(model_dir)
    base = model_json.parent if model_json is not None else Path(model_dir)
    refs = data.get("FileReferences") or {}
    entries: list[tuple[str, Path]] = []
    used: set[str] = set()

    for item in refs.get("Expressions") or []:
        if not isinstance(item, dict):
            continue
        path = _resolve_ref(base, str(item.get("File") or ""))
        if path is None:
            continue
        name = str(item.get("Name") or path.name[: -len(".exp3.json")])
        alias = EXPRESSION_ALIASES.get(name) or name
        entries.append((_unique_key(alias, used), path))

    if not entries:
        try:
            files = sorted(base.rglob("*.exp3.json"))
        except OSError:
            files = []
        for path in files:
            name = path.name[: -len(".exp3.json")]
            alias = EXPRESSION_ALIASES.get(name)
            if alias is None:
                continue
            entries.append((_unique_key(alias, used), path))
    return entries


def _expand_aliases(clip: dict, available: set[str] | None) -> dict:
    """给模型真正存在的等价参数补一份同样的关键帧。"""
    if not available:
        return clip
    keys = []
    for key in clip.get("keys") or []:
        if not isinstance(key, dict):
            continue
        params = dict(key.get("params") or {})
        for source, targets in PARAM_ALIASES.items():
            if source not in params:
                continue
            value = params[source]
            for target in targets:
                if target in available and target not in params:
                    params[target] = value
        keys.append({"t": key.get("t", 0.0), "params": params})
    expanded = dict(clip)
    expanded["keys"] = keys
    return expanded


def _filter_clip(clip: dict, available: set[str] | None) -> dict | None:
    """只保留模型真有的参数；一个都不剩就丢掉这段动作。"""
    if available is None:
        return clip
    keys = []
    for key in clip.get("keys") or []:
        if not isinstance(key, dict):
            continue
        params = {
            str(pid): value
            for pid, value in (key.get("params") or {}).items()
            if str(pid) in available
        }
        if params:
            keys.append({"t": float(key.get("t", 0.0)), "params": params})
    if not keys:
        return None
    return {
        "duration": float(clip.get("duration") or 1.0),
        "loop": bool(clip.get("loop")),
        "keys": keys,
        "snap": list(clip.get("snap") or ()),
    }


def _sample_track(
    times: list[float], values: list[float], points: list[float], periodic: bool = False
) -> list[float]:
    """C1 连续的 Hermite 采样，避免每个关键帧都停一下。"""
    if not times:
        return []
    if len(times) == 1:
        return [values[0] for _ in points]

    slopes: list[float] = []
    for index, moment in enumerate(times):
        if index == 0:
            span = times[1] - times[0]
            slope = (values[1] - values[0]) / span if span > 0 else 0.0
        elif index == len(times) - 1:
            span = times[-1] - times[-2]
            slope = (values[-1] - values[-2]) / span if span > 0 else 0.0
        else:
            span = times[index + 1] - times[index - 1]
            slope = (values[index + 1] - values[index - 1]) / span if span > 0 else 0.0
        slopes.append(slope)

    # 循环动作在首尾接缝处保持同一斜率，否则每次循环都会“顿一下”。
    if periodic and len(times) >= 3:
        span = (times[1] - times[0]) + (times[-1] - times[-2])
        if span > 0:
            seam = (values[1] - values[-2]) / span
            slopes[0] = seam
            slopes[-1] = seam


    result: list[float] = []
    for moment in points:
        if moment <= times[0]:
            result.append(values[0])
            continue
        if moment >= times[-1]:
            result.append(values[-1])
            continue
        index = max(0, min(len(times) - 2, bisect.bisect_right(times, moment) - 1))
        t0, t1 = times[index], times[index + 1]
        span = t1 - t0
        if span <= 0:
            result.append(values[index + 1])
            continue
        u = (moment - t0) / span
        u2 = u * u
        u3 = u2 * u
        h00 = 2 * u3 - 3 * u2 + 1
        h10 = u3 - 2 * u2 + u
        h01 = -2 * u3 + 3 * u2
        h11 = u3 - u2
        value = (
            h00 * values[index]
            + h10 * span * slopes[index]
            + h01 * values[index + 1]
            + h11 * span * slopes[index + 1]
        )
        # 允许一点自然过冲，但不让它飞出去。
        low = min(values[index], values[index + 1])
        high = max(values[index], values[index + 1])
        margin = (high - low) * 0.15
        result.append(max(low - margin, min(high + margin, value)))
    return result


def smooth_clip(clip: dict, rate: float = SMOOTH_RATE) -> dict:
    """把稀疏关键帧重采样成高频曲线，动作不会在每个关键帧停顿。"""
    duration = max(0.05, float(clip.get("duration") or 1.0))
    tracks: dict[str, list[tuple[float, float]]] = {}
    for key in clip.get("keys") or []:
        if not isinstance(key, dict):
            continue
        try:
            at = float(key.get("t", 0.0))
        except (TypeError, ValueError):
            continue
        for pid, raw in (key.get("params") or {}).items():
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            tracks.setdefault(str(pid), []).append((at, value))
    if not tracks:
        return clip

    cleaned: dict[str, list[tuple[float, float]]] = {}
    for pid, points in tracks.items():
        points.sort(key=lambda item: item[0])
        merged: list[tuple[float, float]] = []
        for at, value in points:
            if merged and abs(at - merged[-1][0]) < 1e-6:
                merged[-1] = (at, value)
            else:
                merged.append((at, value))
        cleaned[pid] = merged

    count = max(2, int(round(duration * max(10.0, rate))) + 1)
    moments = [duration * index / (count - 1) for index in range(count)]
    loop = bool(clip.get("loop"))
    series: dict[str, list[float]] = {}
    for pid, points in cleaned.items():
        times = [item[0] for item in points]
        values = [item[1] for item in points]
        if times[0] > 0:
            times.insert(0, 0.0)
            values.insert(0, values[0])
        if times[-1] < duration:
            times.append(duration)
            values.append(values[-1])
        series[pid] = _sample_track(times, values, moments, periodic=loop)

    # Movement1/2/3 是手型开关，不是连续通道。插值到 0.5 会同时显示两只手，
    # 看起来就是残影，所以这里按中点硬切。
    for pid in SNAP_PARAMS:
        if pid in series:
            series[pid] = [1.0 if value >= 0.5 else 0.0 for value in series[pid]]

    keys = []
    for index, moment in enumerate(moments):
        params = {
            pid: round(values[index], 4)
            for pid, values in series.items()
            if index < len(values)
        }
        if params:
            keys.append({"t": round(moment, 4), "params": params})
    return {
        "duration": round(duration, 3),
        "loop": loop,
        "keys": keys,
        "snap": list(clip.get("snap") or ()),
    }


# 主动点菜单里的姿势动作时，播完要停在最后一帧；自动待机播放则只播一遍就退回。
# 宿主目前会在非循环动作播完后淡出，所以这里生成一份「末帧一直保持」的变体：
# 在原时长后面接一个 24 小时的同值尾帧，渲染时就会稳定停在最后姿势。
HOLD_SECONDS = 24 * 60 * 60
HOLD_SUFFIX = "@hold"
HOLD_CLIPS = (
    "dress",
    "dress_short",
    "chibi",
    "chibi_normal",
    "tail_hide",
    "tail_show",
    "hand_up",
    "hand_down",
    "foxtail_out",
    "foxtail_in",
    "point_out",
    "point_in",
    "special",
)


def hold_clip(clip: dict, seconds: float = HOLD_SECONDS) -> dict:
    duration = max(0.05, float(clip.get("duration") or 1.0))
    keys = [key for key in clip.get("keys") or [] if isinstance(key, dict)]
    if not keys:
        return clip
    last = keys[-1].get("params") or {}
    if not last:
        return clip
    held = keys + [{"t": round(duration + seconds, 3), "params": dict(last)}]
    return {
        "duration": round(duration + seconds, 3),
        "loop": False,
        "keys": held,
        "snap": list(clip.get("snap") or ()),
    }


def build_hold_clips(clips: dict) -> dict:
    """给姿势类动作各生成一个停在末帧的变体；循环动作不需要。"""
    out: dict[str, dict] = {}
    for name in HOLD_CLIPS:
        clip = clips.get(name)
        if not clip or clip.get("loop"):
            continue
        out[name + HOLD_SUFFIX] = hold_clip(clip)
    return out


def procedural_clips(available_params: set[str] | None = None) -> dict[str, dict]:
    clips: dict[str, dict] = {}

    clips["nod"] = _clip(
        1.35,
        [
            (0.0, {"AngleX": 0, "ParamAngleX": 0}),
            (0.24, {"AngleX": -11, "ParamAngleX": -12, "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7}),
            (0.52, {"AngleX": -1, "ParamAngleX": -1, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
            (0.8, {"AngleX": -10, "ParamAngleX": -11, "ParamEyeLOpen": 0.72, "ParamEyeROpen": 0.72}),
            (1.35, {"AngleX": 0, "ParamAngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    # 起跳时间对齐宿主的整图跳跃：0.12s 下蹲，0.42s 升到最高，0.62s 落地。
    clips["hop"] = _clip(
        0.9,
        [
            (0.0, {"AngleX": 0, "Param36": 0, "ParamBodyAngleX": 0}),
            (0.12, {"AngleX": -5, "Param36": 0.15, "ParamBodyAngleX": -2,
                    "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55}),
            (0.42, {"AngleX": 7, "Param36": 0.6, "ParamBodyAngleX": 3,
                    "ParamMouthOpenY": 0.3, "ParamMouthForm": 0.6,
                    "ParamEyeLOpen": 0.95, "ParamEyeROpen": 0.95}),
            (0.62, {"AngleX": -3, "Param36": 0.3, "ParamBodyAngleX": -1,
                    "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7}),
            (0.9, {"AngleX": 0, "Param36": 0, "ParamBodyAngleX": 0, "ParamMouthForm": 0.15,
                   "ParamMouthOpenY": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    clips["shake"] = _clip(
        1.45,
        [
            (0.0, {"AngleY": 0, "ParamAngleY": 0}),
            (0.22, {"AngleY": 14, "ParamAngleY": 14, "AngleZ": -3}),
            (0.48, {"AngleY": -14, "ParamAngleY": -14, "AngleZ": 3}),
            (0.74, {"AngleY": 11, "ParamAngleY": 11, "AngleZ": -2}),
            (1.0, {"AngleY": -9, "ParamAngleY": -9, "AngleZ": 2}),
            (1.45, {"AngleY": 0, "ParamAngleY": 0, "AngleZ": 0}),
        ],
    )

    clips["tilt"] = _clip(
        2.8,
        [
            (0.0, {"AngleZ": 0, "ParamAngleZ": 0, "CheeckPuff": 0}),
            (0.55, {"AngleZ": 17, "ParamAngleZ": 13, "AngleY": -4, "CheeckPuff": 0.35,
                    "ParamMouthForm": 0.55, "ParamEyeLOpen": 0.88, "ParamEyeROpen": 0.88}),
            (1.9, {"AngleZ": 16, "ParamAngleZ": 12, "AngleY": -4, "CheeckPuff": 0.32,
                   "ParamMouthForm": 0.5}),
            (2.8, {"AngleZ": 0, "ParamAngleZ": 0, "AngleY": 0, "CheeckPuff": 0,
                   "ParamMouthForm": 0.1}),
        ],
    )

    clips["wave"] = _clip(3.0, _wave_frames(high=0.72))
    clips["greet"] = _clip(3.2, _wave_frames(high=0.9, start=0.15, end=2.8))
    clips["wave_big"] = _clip(
        3.4,
        _wave_frames(high=1.0, start=0.12, end=3.0)
        + [(3.4, {"Param36": 0, "Movement2": 0, "AngleZ": 0, "AngleY": 0})],
    )

    clips["think"] = _clip(
        3.2,
        [
            (0.0, {"Movement2": 0, "AngleZ": 0, "ParamEyeBallX": 0, "ParamEyeBallY": 0}),
            (0.45, {"Movement2": 1, "AngleZ": -8, "AngleX": 2, "ParamEyeBallX": 0.55,
                    "ParamEyeBallY": 0.4, "ParamBrowLY": 0.28, "ParamBrowRY": 0.12,
                    "ParamMouthForm": -0.15, "CheeckPuff": 0.12}),
            (1.4, {"Movement2": 1, "AngleZ": -7, "AngleY": 4, "ParamEyeBallX": 0.62,
                   "ParamEyeBallY": 0.35, "ParamMouthForm": -0.2}),
            (2.2, {"Movement2": 1, "AngleZ": -9, "AngleY": -3, "ParamEyeBallX": 0.4,
                   "ParamEyeBallY": 0.5, "ParamMouthForm": -0.1}),
            (2.8, {"Movement2": 1, "AngleZ": -5, "ParamEyeBallX": 0.1, "ParamEyeBallY": 0.2}),
            (3.2, {"Movement2": 0, "AngleZ": 0, "AngleY": 0, "AngleX": 0,
                   "ParamEyeBallX": 0, "ParamEyeBallY": 0, "ParamBrowLY": 0,
                   "ParamBrowRY": 0, "ParamMouthForm": 0, "CheeckPuff": 0}),
        ],
    )

    clips["pout"] = _clip(
        2.9,
        [
            (0.0, {"CheeckPuff": 0, "CheeckPuff2": 0}),
            (0.35, {"CheeckPuff": 1, "CheeckPuff2": 1, "ParamBrowLY": -0.4,
                    "ParamBrowRY": -0.4, "ParamMouthForm": -0.65, "AngleY": 4, "AngleZ": -4}),
            (0.9, {"CheeckPuff": 1, "CheeckPuff2": 1, "AngleY": -6, "AngleZ": 2}),
            (1.45, {"CheeckPuff": 1, "CheeckPuff2": 1, "AngleY": 5, "AngleZ": -2}),
            (2.1, {"CheeckPuff": 1, "CheeckPuff2": 1, "AngleY": -3, "AngleZ": 1}),
            (2.9, {"CheeckPuff": 0, "CheeckPuff2": 0, "ParamBrowLY": 0, "ParamBrowRY": 0,
                   "ParamMouthForm": 0, "AngleY": 0, "AngleZ": 0}),
        ],
    )

    clips["surprise"] = _clip(
        1.7,
        [
            (0.0, {"ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthOpenY": 0}),
            (0.14, {"ParamEyeLOpen": 1.45, "ParamEyeROpen": 1.45, "ShockL": 0.85,
                    "ShockR": 0.85, "ParamMouthOpenY": 0.68, "AngleX": 9,
                    "ParamBodyAngleX": 5, "ParamBrowLY": 0.35, "ParamBrowRY": 0.35}),
            (0.9, {"ParamEyeLOpen": 1.4, "ParamEyeROpen": 1.4, "ShockL": 0.7,
                   "ShockR": 0.7, "ParamMouthOpenY": 0.55, "AngleX": 8, "AngleZ": 3}),
            (1.7, {"ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ShockL": 0, "ShockR": 0,
                   "ParamMouthOpenY": 0, "AngleX": 0, "ParamBodyAngleX": 0, "AngleZ": 0,
                   "ParamBrowLY": 0, "ParamBrowRY": 0}),
        ],
    )

    clips["shy"] = _clip(
        3.6,
        [
            (0.0, {"key7": 0, "AngleX": 0, "AngleZ": 0, "ParamEyeBallX": 0}),
            (0.45, {"key7": 1, "AngleX": -8, "AngleZ": 7, "AngleY": 5,
                    "ParamEyeBallX": -0.58, "ParamEyeBallY": -0.28, "ParamMouthForm": 0.38,
                    "CheeckPuff": 0.35}),
            (1.6, {"key7": 1, "AngleX": -6, "AngleZ": 6, "AngleY": -4,
                   "ParamEyeBallX": -0.62, "ParamEyeBallY": -0.2, "ParamMouthForm": 0.32}),
            (2.6, {"key7": 1, "AngleX": -4, "AngleZ": 8, "AngleY": 3,
                   "ParamEyeBallX": -0.45, "ParamMouthForm": 0.45}),
            (3.6, {"key7": 0, "AngleX": 0, "AngleZ": 0, "AngleY": 0, "ParamEyeBallX": 0,
                   "ParamEyeBallY": 0, "ParamMouthForm": 0.1, "CheeckPuff": 0}),
        ],
    )

    clips["love"] = _clip(
        3.4,
        [
            (0.0, {"key4": 0, "AngleZ": 0, "AngleY": 0}),
            (0.4, {"key4": 1, "AngleZ": 10, "AngleY": 5, "ParamMouthForm": 0.65,
                   "ParamEyeLOpen": 0.95, "ParamEyeROpen": 0.95, "CheeckPuff": 0.28}),
            (1.2, {"key4": 1, "AngleZ": 6, "AngleY": -5, "ParamMouthForm": 0.55}),
            (2.0, {"key4": 1, "AngleZ": 11, "AngleY": 4, "ParamMouthForm": 0.68}),
            (2.8, {"key4": 1, "AngleZ": 5, "AngleY": -2, "ParamMouthForm": 0.5}),
            (3.4, {"key4": 0, "AngleZ": 0, "AngleY": 0, "ParamMouthForm": 0.15,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "CheeckPuff": 0}),
        ],
    )

    clips["dizzy"] = _clip(
        3.9,
        [
            (0.0, {"key3": 0, "key4": 0, "key5": 0, "key6": 0}),
            (0.25, {"key3": 1, "key4": 0, "key5": 0, "key6": 0, "AngleZ": 8, "AngleY": 6,
                    "ParamMouthOpenY": 0.25}),
            (0.8, {"key3": 1, "AngleZ": -10, "AngleY": -7, "ParamMouthOpenY": 0.3}),
            (1.35, {"key3": 1, "AngleZ": 9, "AngleY": 6, "ParamMouthOpenY": 0.22}),
            (1.9, {"key3": 1, "AngleZ": -8, "AngleY": -5, "ParamMouthOpenY": 0.28}),
            (2.5, {"key3": 1, "AngleZ": 5, "AngleY": 4, "ParamMouthOpenY": 0.18}),
            (3.1, {"key3": 1, "AngleZ": -3, "AngleY": -2, "ParamMouthOpenY": 0.12}),
            (3.9, {"key3": 0, "AngleZ": 0, "AngleY": 0, "ParamMouthOpenY": 0}),
        ],
    )

    clips["squint"] = _clip(
        2.6,
        [
            (0.0, {"key5": 0, "key3": 0, "key6": 0}),
            (0.18, {"key5": 1, "key3": 0, "key6": 0, "AngleZ": -5, "Param36": 0.25,
                    "ParamMouthForm": 0.4}),
            (0.55, {"key5": 1, "AngleZ": 6, "AngleY": 4, "Param36": 0.35}),
            (0.95, {"key5": 1, "AngleZ": -6, "AngleY": -4, "Param36": 0.3}),
            (1.4, {"key5": 1, "AngleZ": 4, "AngleY": 2, "Param36": 0.2}),
            (2.0, {"key5": 1, "AngleZ": -2, "Param36": 0.1}),
            (2.6, {"key5": 0, "AngleZ": 0, "AngleY": 0, "Param36": 0, "ParamMouthForm": 0.1}),
        ],
    )

    clips["cry"] = _clip(
        3.7,
        [
            (0.0, {"key2": 0, "ParamMouthForm": 0}),
            (0.3, {"key2": 1, "ParamMouthForm": -0.75, "ParamBrowLY": 0.48,
                   "ParamBrowRY": 0.48, "AngleX": -8, "AngleZ": 3, "ParamMouthOpenY": 0.3}),
            (1.0, {"key2": 1, "AngleX": -6, "AngleZ": -3, "ParamMouthOpenY": 0.4}),
            (1.7, {"key2": 1, "AngleX": -9, "AngleZ": 2, "ParamMouthOpenY": 0.28}),
            (2.5, {"key2": 1, "AngleX": -5, "AngleZ": -2, "ParamMouthOpenY": 0.22}),
            (3.7, {"key2": 0, "ParamMouthForm": 0.05, "ParamBrowLY": 0, "ParamBrowRY": 0,
                   "AngleX": 0, "AngleZ": 0, "ParamMouthOpenY": 0}),
        ],
    )

    clips["deadpan"] = _clip(
        3.2,
        [
            (0.0, {"key1": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
            (0.28, {"key1": 1, "ParamEyeLOpen": 0.58, "ParamEyeROpen": 0.58,
                    "ParamMouthForm": -0.28, "AngleX": -5, "AngleZ": -3}),
            (1.8, {"key1": 1, "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55,
                   "ParamMouthForm": -0.25, "AngleX": -4, "AngleZ": -2}),
            (3.2, {"key1": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthForm": 0,
                   "AngleX": 0, "AngleZ": 0}),
        ],
    )

    clips["sweat"] = _clip(
        2.9,
        [
            (0.0, {"key13": 0}),
            (0.22, {"key13": 1, "ParamEyeLOpen": 1.22, "ParamEyeROpen": 1.22,
                    "ParamMouthForm": 0.22, "ParamBrowLY": 0.28, "ParamBrowRY": 0.28,
                    "AngleZ": 3}),
            (0.55, {"key13": 1, "AngleZ": -20}),  # 让头快速一僵，数值会被夹紧到上限
            (0.62, {"key13": 1, "AngleZ": 3}),
            (1.6, {"key13": 1, "AngleZ": -2, "ParamEyeLOpen": 1.18, "ParamEyeROpen": 1.18}),
            (2.9, {"key13": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthForm": 0,
                   "ParamBrowLY": 0, "ParamBrowRY": 0, "AngleZ": 0}),
        ],
    )

    clips["stretch"] = _clip(
        3.6,
        [
            (0.0, {"Param36": 0, "Movement2": 0}),
            (0.5, {"Param36": 1, "Movement2": 1, "LArm": 0.35,
                   "LForearm": -0.3, "AngleX": 9, "AngleY": -4, "ParamMouthOpenY": 0.5,
                   "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55, "ParamBodyAngleX": 4}),
            (1.3, {"Param36": 1, "LArm": 0.5, "LForearm": -0.45, "AngleX": 11,
                   "AngleZ": 4, "ParamMouthOpenY": 0.6}),
            (2.1, {"Param36": 1, "LArm": 0.28, "LForearm": -0.2, "AngleX": 7,
                   "AngleZ": -3, "ParamMouthOpenY": 0.35}),
            (2.8, {"Param36": 0.4, "LArm": 0.1, "LForearm": 0, "AngleX": 2,
                   "ParamMouthOpenY": 0.12, "ParamEyeLOpen": 0.85, "ParamEyeROpen": 0.85}),
            (3.6, {"Param36": 0, "Movement2": 0, "LArm": 0, "AngleX": 0, "AngleZ": 0,
                   "ParamMouthOpenY": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1,
                   "ParamBodyAngleX": 0}),
        ],
    )

    clips["bow"] = _clip(
        2.9,
        [
            (0.0, {"AngleX": 0, "ParamAngleX": 0}),
            (0.55, {"AngleX": -26, "ParamAngleX": -10, "AngleY": 2,
                    "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55, "ParamMouthForm": -0.1}),
            (1.8, {"AngleX": -25, "ParamAngleX": -10, "AngleY": 2,
                   "ParamEyeLOpen": 0.52, "ParamEyeROpen": 0.52}),
            (2.9, {"AngleX": 0, "ParamAngleX": 0, "AngleY": 0,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthForm": 0.1}),
        ],
    )

    # Clothes1：1 = 短裙，-1 = 长裙。换装动作要停在长裙，不能换完又弹回短裙。
    clips["dress"] = _clip(
        4.6,
        [
            (0.0, {"Clothes1": 1, "AngleZ": 0, "AngleY": 0}),
            (0.8, {"Clothes1": -1, "AngleZ": 12, "AngleY": 10, "ParamMouthForm": 0.55,
                   "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
            (1.5, {"Clothes1": -1, "AngleZ": -14, "AngleY": -12, "ParamBodyAngleZ": -7}),
            (2.2, {"Clothes1": -1, "AngleZ": 13, "AngleY": 11, "ParamBodyAngleZ": 6}),
            (2.9, {"Clothes1": -1, "AngleZ": -10, "AngleY": -8, "ParamBodyAngleZ": -4}),
            (3.6, {"Clothes1": -1, "AngleZ": 5, "AngleY": 4, "ParamBodyAngleZ": 2}),
            (4.0, {"Clothes1": -1, "AngleZ": 0, "AngleY": 0, "ParamBodyAngleZ": 0,
                   "ParamMouthForm": 0.2}),
            (4.6, {"Clothes1": -1, "AngleZ": 0, "AngleY": 0, "ParamMouthForm": 0}),
        ],
    )

    # 换回短裙：长裙之后想单独换回来，不用把所有动作都清掉。
    clips["dress_short"] = _clip(
        3.6,
        [
            (0.0, {"Clothes1": -1, "AngleZ": 0, "AngleY": 0}),
            (0.7, {"Clothes1": 1, "AngleZ": 12, "AngleY": 9, "ParamMouthForm": 0.5,
                   "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
            (1.3, {"Clothes1": 1, "AngleZ": -13, "AngleY": -10, "ParamBodyAngleZ": -6}),
            (1.9, {"Clothes1": 1, "AngleZ": 11, "AngleY": 8, "ParamBodyAngleZ": 5}),
            (2.5, {"Clothes1": 1, "AngleZ": -7, "AngleY": -5, "ParamBodyAngleZ": -3}),
            (3.0, {"Clothes1": 1, "AngleZ": 3, "AngleY": 2, "ParamBodyAngleZ": 1}),
            (3.6, {"Clothes1": 1, "AngleZ": 0, "AngleY": 0, "ParamBodyAngleZ": 0,
                   "ParamMouthForm": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    # Clothes2：0 = 原体型，1 = 最小。变小之后要停在小体型。
    clips["chibi"] = _clip(
        3.9,
        [
            (0.0, {"Clothes2": 0, "AngleZ": 0}),
            (0.45, {"Clothes2": 1, "AngleZ": 10, "AngleY": 5, "ParamMouthForm": 0.6,
                    "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
            (1.0, {"Clothes2": 1, "AngleZ": -12, "AngleY": -6, "ParamMouthForm": 0.7}),
            (1.55, {"Clothes2": 1, "AngleZ": 10, "AngleY": 5, "ParamMouthForm": 0.55}),
            (2.1, {"Clothes2": 1, "AngleZ": -7, "AngleY": -3}),
            (2.6, {"Clothes2": 1, "AngleZ": 4, "AngleY": 2}),
            (3.1, {"Clothes2": 1, "AngleZ": 0, "AngleY": 0, "ParamMouthForm": 0.3}),
            (3.5, {"Clothes2": 1, "AngleZ": 0, "AngleY": 0}),
            (3.9, {"Clothes2": 1, "ParamMouthForm": 0}),
        ],
    )

    # 变回原来的体型：单独点「变小」之后也能恢复。
    clips["chibi_normal"] = _clip(
        2.8,
        [
            (0.0, {"Clothes2": 1, "AngleZ": 0}),
            (0.45, {"Clothes2": 1, "AngleZ": 9, "AngleY": 4, "ParamMouthForm": 0.5,
                    "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
            (0.9, {"Clothes2": 0, "AngleZ": -9, "AngleY": -4, "ParamMouthForm": 0.6}),
            (1.5, {"Clothes2": 0, "AngleZ": 5, "AngleY": 2, "ParamMouthForm": 0.3}),
            (2.1, {"Clothes2": 0, "AngleZ": -2, "ParamMouthForm": 0.1}),
            (2.8, {"Clothes2": 0, "AngleZ": 0, "AngleY": 0, "ParamMouthForm": 0,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    clips["sigh"] = _clip(
        3.0,
        [
            (0.0, {"AngleX": 0, "ParamMouthOpenY": 0}),
            (0.55, {"AngleX": -11, "ParamBodyAngleX": -5, "ParamEyeLOpen": 0.62,
                    "ParamEyeROpen": 0.62, "ParamMouthOpenY": 0.42, "ParamBrowLY": 0.2,
                    "ParamBrowRY": 0.2}),
            (1.6, {"AngleX": -9, "ParamBodyAngleX": -4, "ParamEyeLOpen": 0.6,
                   "ParamEyeROpen": 0.6, "ParamMouthOpenY": 0.18}),
            (3.0, {"AngleX": 0, "ParamBodyAngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1,
                   "ParamMouthOpenY": 0, "ParamBrowLY": 0, "ParamBrowRY": 0}),
        ],
    )

    clips["sneeze"] = _clip(
        2.1,
        [
            (0.0, {"AngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
            (0.28, {"AngleX": -6, "ParamEyeLOpen": 0.3, "ParamEyeROpen": 0.3,
                    "ParamMouthOpenY": 0.15, "ParamBrowLY": 0.2, "ParamBrowRY": 0.2}),
            (0.5, {"AngleX": 14, "ParamEyeLOpen": 0.0, "ParamEyeROpen": 0.0,
                   "ParamMouthOpenY": 0.75, "ParamBodyAngleX": 7}),
            (0.75, {"AngleX": -7, "ParamEyeLOpen": 0.35, "ParamEyeROpen": 0.35,
                    "ParamMouthOpenY": 0.3}),
            (1.2, {"AngleX": 2, "ParamEyeLOpen": 0.8, "ParamEyeROpen": 0.8,
                   "ParamMouthOpenY": 0.1}),
            (2.1, {"AngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1,
                   "ParamMouthOpenY": 0, "ParamBodyAngleX": 0, "ParamBrowLY": 0,
                   "ParamBrowRY": 0}),
        ],
    )

    sing_frames = [(0.0, {"ParamMouthOpenY": 0, "ParamMouthForm": 0.3, "AngleZ": 0,
                          "AngleX": 0, "ParamBodyAngleZ": 0, "ParamBodyAngleX": 0})]
    for index in range(10):
        at = 0.3 + index * 0.36
        sing_frames.append(
            (
                at,
                {
                    "ParamMouthOpenY": 0.18 if index % 2 else 0.52,
                    "ParamMouthForm": 0.45,
                    "AngleZ": 3 if index % 2 else -3,
                    "AngleY": 3 if index % 3 == 0 else -2,
                    "AngleX": 2 if index % 2 else -4,
                    "ParamBodyAngleZ": 5 if index % 2 else -5,
                    "ParamBodyAngleX": 2 if index % 2 else -2,
                    "Param36": 0.18,
                },
            )
        )
    sing_frames.append((4.3, {"ParamMouthOpenY": 0, "ParamMouthForm": 0.1,
                              "AngleZ": 0, "AngleY": 0, "AngleX": 0,
                              "ParamBodyAngleZ": 0, "ParamBodyAngleX": 0,
                              "Param36": 0}))
    clips["sing"] = _clip(4.4, sing_frames)

    dance_frames = [(0.0, {"AngleZ": 0, "AngleY": 0, "Param36": 0})]
    for index in range(8):
        at = 0.35 + index * 0.55
        side = 1 if index % 2 == 0 else -1
        dance_frames.extend(
            [
                (
                    at,
                    {
                        "AngleZ": 12 * side,
                        "AngleY": 9 * side,
                        "ParamBodyAngleZ": 6 * side,
                        "AngleX": -3,
                        "Param36": 0.85 if side > 0 else 0.15,
                        "Movement2": 1 if side > 0 else 0,
                        "ParamMouthForm": 0.6,
                        "CheeckPuff": 0.2,
                    },
                ),
                (
                    at + 0.26,
                    {
                        "AngleZ": -4 * side,
                        "AngleY": -3 * side,
                        "ParamBodyAngleZ": -2 * side,
                        "Param36": 0.3,
                    },
                ),
            ]
        )
    dance_frames.append((4.9, {"AngleZ": 0, "AngleY": 0, "ParamBodyAngleZ": 0, "AngleX": 0,
                               "Param36": 0, "Movement2": 0,
                               "ParamMouthForm": 0.2, "CheeckPuff": 0}))
    clips["dance"] = _clip(5.0, dance_frames)

    clips["glance"] = _clip(
        3.4,
        [
            (0.0, {"ParamEyeBallX": 0, "ParamEyeBallY": 0, "AngleY": 0}),
            (0.55, {"ParamEyeBallX": -0.85, "ParamEyeBallY": 0.15, "AngleY": -9,
                    "AngleZ": 3}),
            (1.35, {"ParamEyeBallX": -0.9, "ParamEyeBallY": -0.1, "AngleY": -10,
                    "AngleZ": 4}),
            (2.1, {"ParamEyeBallX": 0.8, "ParamEyeBallY": 0.12, "AngleY": 9,
                   "AngleZ": -4}),
            (2.8, {"ParamEyeBallX": 0.3, "ParamEyeBallY": 0, "AngleY": 3, "AngleZ": 0}),
            (3.4, {"ParamEyeBallX": 0, "ParamEyeBallY": 0, "AngleY": 0, "AngleZ": 0}),
        ],
    )

    clips["proud"] = _clip(
        3.4,
        [
            (0.0, {"AngleX": 0, "CheeckPuff": 0, "ParamMouthForm": 0}),
            (0.5, {"AngleX": -6, "CheeckPuff": 0.22, "ParamMouthForm": 0.62,
                   "ParamEyeLOpen": 0.62, "ParamEyeROpen": 0.62, "AngleZ": -4}),
            (1.8, {"AngleX": -5, "CheeckPuff": 0.2, "ParamMouthForm": 0.58,
                   "ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6, "AngleZ": -3}),
            (3.0, {"AngleX": -3, "CheeckPuff": 0.1, "ParamMouthForm": 0.4,
                   "ParamEyeLOpen": 0.8, "ParamEyeROpen": 0.8, "AngleZ": 0}),
            (3.4, {"AngleX": 0, "CheeckPuff": 0, "ParamMouthForm": 0.1,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    clips["listen"] = _clip(
        2.6,
        [
            (0.0, {"AngleZ": 0, "ParamEyeBallX": 0}),
            (0.45, {"AngleZ": 10, "AngleY": -5, "ParamEyeBallX": 0.2, "ParamMouthForm": 0.15,
                    "ParamEyeLOpen": 0.95, "ParamEyeROpen": 0.95}),
            (1.6, {"AngleZ": 8, "AngleY": -3, "ParamEyeBallX": 0.25, "ParamMouthForm": 0.12}),
            (2.6, {"AngleZ": 0, "AngleY": 0, "ParamEyeBallX": 0, "ParamMouthForm": 0}),
        ],
    )

    # Clothes5：1 = 尾巴露出，0 及以下 = 收起。藏尾巴要停在收起状态。
    clips["tail_hide"] = _clip(
        2.6,
        [
            (0.0, {"Clothes5": 1}),
            (0.4, {"Clothes5": -1, "AngleZ": 6, "ParamMouthForm": 0.4, "CheeckPuff": 0.2}),
            (1.8, {"Clothes5": -1, "AngleZ": -4, "CheeckPuff": 0.15}),
            (2.6, {"Clothes5": -1, "AngleZ": 0, "ParamMouthForm": 0.1, "CheeckPuff": 0}),
        ],
    )

    clips["tail_show"] = _clip(
        2.2,
        [
            (0.0, {"Clothes5": -1, "AngleZ": 0}),
            (0.3, {"Clothes5": 1, "AngleZ": 9, "ParamMouthForm": 0.6, "ParamEyeLOpen": 0.85,
                   "ParamEyeROpen": 0.85}),
            (1.5, {"Clothes5": 1, "AngleZ": 4, "ParamMouthForm": 0.4}),
            (2.2, {"Clothes5": 1, "AngleZ": 0, "ParamMouthForm": 0.1,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    result: dict[str, dict] = {}
    articulated = _can_articulate(available_params)
    static_rig = _is_static_rig(available_params)
    for name, clip in clips.items():
        signature = SIGNATURE_PARAMS.get(name)
        if available_params is not None and signature and signature not in available_params:
            continue
        # 手臂/躯干做不了的动作，不放进菜单。
        if available_params is not None and name in BODY_ACTIONS and not articulated:
            continue
        # 静态立绘连头部俯仰都没有，点头也只能靠平移硬凑，不如不显示。
        if static_rig and name == "nod":
            continue
        expanded = _expand_aliases(clip, available_params)
        filtered = _filter_clip(expanded, available_params)
        if filtered is not None:
            result[name] = smooth_clip(filtered)
    return result


def expression_clips(
    model_dir: Path, available_params: set[str] | None = None
) -> dict[str, dict]:
    """把 Exp3 里的表情转成常驻姿势。

    available_params 为 None 时只保留已知前缀（兼容旧模型）；
    能读到模型参数表时，按真实参数过滤。
    """
    expressions: dict[str, dict] = {}
    root = Path(model_dir)
    if not root.is_dir():
        return expressions
    for alias, path in model_expression_files(root):
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        params: dict[str, float] = {}
        for item in data.get("Parameters", []):
            if not isinstance(item, dict):
                continue
            pid = str(item.get("Id") or "").strip()
            if not pid:
                continue
            if available_params is not None:
                if pid not in available_params:
                    continue
            elif not pid.startswith(KEEP_PARAM_PREFIXES):
                continue
            try:
                value = float(item.get("Value", 0.0))
            except (TypeError, ValueError):
                continue
            blend = str(item.get("Blend") or "Add")
            if blend == "Multiply":
                value = 1.0 - value
            params[pid] = value
        if params:
            expressions[alias] = _clip(1.0, [(0.0, params)], loop=True)
    return expressions


def motion_clip(path: Path, name: str, *, loop: bool | None = None) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    meta = data.get("Meta") or {}
    try:
        duration = float(meta.get("Duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    tracks: dict[str, list[tuple[float, float]]] = {}
    max_time = 0.0
    for curve in data.get("Curves", []):
        if not isinstance(curve, dict) or curve.get("Target") != "Parameter":
            continue
        pid = str(curve.get("Id") or "").strip()
        segments = curve.get("Segments")
        if not pid or not isinstance(segments, list):
            continue
        samples = _segments_to_samples(segments)
        if not samples:
            continue
        tracks[pid] = samples
        max_time = max(max_time, samples[-1][0])

    if duration <= 0:
        duration = max_time or 1.0

    frames: dict[float, dict[str, float]] = {}
    for pid, samples in tracks.items():
        if samples[0][0] > 0.0:
            frames.setdefault(0.0, {})[pid] = samples[0][1]
        for at, value in samples:
            key = round(at, 3)
            frames.setdefault(key, {})[pid] = round(value, 4)
    ordered = [(at, params) for at, params in sorted(frames.items())]
    if loop is None:
        loop = bool(meta.get("Loop") or False)
    return _clip(duration, ordered, loop=loop)


def _segments_to_samples(segments: list, rate: float = 30.0) -> list[tuple[float, float]]:
    if len(segments) < 2:
        return []
    try:
        start_t = float(segments[0])
        start_v = float(segments[1])
    except (TypeError, ValueError):
        return []
    samples: list[tuple[float, float]] = [(start_t, start_v)]
    t0, v0 = start_t, start_v
    index = 2
    guard = 0
    while index < len(segments) and guard < 4096:
        guard += 1
        try:
            kind = int(round(float(segments[index])))
        except (TypeError, ValueError):
            break
        index += 1
        if kind in (0, 2, 3):
            if index + 1 >= len(segments):
                break
            try:
                t1 = float(segments[index])
                v1 = float(segments[index + 1])
            except (TypeError, ValueError):
                break
            index += 2
            if kind == 0:
                _append_linear(samples, t0, v0, t1, v1, rate)
            elif kind == 2:
                samples.append((max(t0, t1 - 1.0 / rate), v0))
                samples.append((t1, v1))
            else:
                samples.append((t0, v1))
                samples.append((max(t0, t1 - 1.0 / rate), v1))
            t0, v0 = t1, v1
        elif kind == 1:
            if index + 5 >= len(segments):
                break
            try:
                # Cubism 的贝塞尔段是 [1, c1t, c1v, c2t, c2v, t1, v1]：
                # 前四个是控制点，最后两个才是终点。之前按「终点在前」
                # 解析，会把控制点当成关键帧，手臂会被甩到错误的位置。
                c1t = float(segments[index])
                c1v = float(segments[index + 1])
                c2t = float(segments[index + 2])
                c2v = float(segments[index + 3])
                t1 = float(segments[index + 4])
                v1 = float(segments[index + 5])
            except (TypeError, ValueError):
                break
            index += 6
            _append_bezier(samples, (t0, v0), (c1t, c1v), (c2t, c2v), (t1, v1), rate)
            t0, v0 = t1, v1
        else:
            break
    return samples


def _append_linear(
    samples: list[tuple[float, float]], t0: float, v0: float, t1: float, v1: float, rate: float
) -> None:
    span = max(1e-6, t1 - t0)
    count = max(1, int(span * rate))
    for step in range(1, count + 1):
        ratio = step / count
        samples.append((t0 + span * ratio, v0 + (v1 - v0) * ratio))


def _append_bezier(
    samples: list[tuple[float, float]],
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    rate: float,
) -> None:
    span = max(1e-6, p3[0] - p0[0])
    count = max(2, int(span * rate))
    for step in range(1, count + 1):
        u = step / count
        mu = 1.0 - u
        t = (
            mu * mu * mu * p0[0]
            + 3 * mu * mu * u * p1[0]
            + 3 * mu * u * u * p2[0]
            + u * u * u * p3[0]
        )
        v = (
            mu * mu * mu * p0[1]
            + 3 * mu * mu * u * p1[1]
            + 3 * mu * u * u * p2[1]
            + u * u * u * p3[1]
        )
        samples.append((t, v))


def build_clips(model_dir: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """返回 (动作, 表情)，按模型自己的参数表和动作表适配。"""
    model_dir = Path(model_dir)
    params = model_parameters(model_dir)
    if params is None:
        params = set(COMMON_PARAMS)
    clips = procedural_clips(params)
    if model_dir.is_dir():
        for alias, path in model_motions(model_dir):
            try:
                # 作者导出的过渡动作（举手、竖手指、狗尾草等）Meta.Loop 也是 true，
                # 但它们是「切到某个手型再停住」，无限循环会变成反复伸手又缩回去。
                # 只有真正的待机动作保留循环。
                clip = motion_clip(path, alias, loop=alias.startswith("idle"))
            except (OSError, ValueError, TypeError):
                continue
            filtered = _filter_clip(clip, params)
            if filtered is not None:
                clips[alias] = smooth_clip(filtered)
    expressions = expression_clips(model_dir, params)
    return clips, expressions


def action_for_host(name: str) -> str | None:
    """桌宠内置动作 → 模型动作。"""
    return {
        "hop": "hop",
        "spin": "dance",
        "stretch": "stretch",
        "shake": "shake",
        "lean": "tilt",
        "nod": "nod",
    }.get(str(name or ""))
