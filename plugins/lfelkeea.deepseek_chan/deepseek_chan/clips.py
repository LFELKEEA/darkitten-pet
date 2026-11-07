"""动作与表情。

两来源：
1. 模型自带的 motion3.json（举手、竖手指、狗尾草、脱捕），转成参数关键帧；
2. 用模型实际可动的参数手写的动作，覆盖头、身体、手臂、手型、嘴、眼睛、服装。
"""

from __future__ import annotations

import json
from pathlib import Path

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


def _clip(duration: float, frames, loop: bool = False) -> dict:
    keys = []
    for at, params in frames:
        keys.append(
            {
                "t": round(max(0.0, float(at)), 3),
                "params": {str(key): round(float(value), 4) for key, value in params.items()},
            }
        )
    return {"duration": round(max(0.05, float(duration)), 3), "loop": bool(loop), "keys": keys}


def _wave_frames(*, high: float, start: float = 0.18, end: float = 2.55) -> list:
    """抬手挥一挥。Param36 是模型里真正能让手臂抬起来的参数。"""
    return [
        (0.0, {"Param36": 0, "Movement1": 0, "Movement3": 0, "AngleZ": 0, "AngleY": 0}),
        (start, {"Param36": high * 0.45, "Movement1": 1, "AngleZ": 4, "AngleY": -2,
                 "ParamMouthForm": 0.45, "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
        (start + 0.28, {"Param36": high, "Movement1": 1, "Movement3": 0, "AngleZ": 7,
                        "AngleY": -3, "ParamMouthForm": 0.6, "CheeckPuff": 0.15}),
        (start + 0.52, {"Param36": high * 0.86, "Movement1": 1, "AngleZ": 4,
                        "LForearm": 0.3, "LHand": 0.2}),
        (start + 0.76, {"Param36": high, "Movement1": 1, "AngleZ": 8, "LForearm": -0.25,
                        "LHand": -0.15}),
        (start + 1.0, {"Param36": high * 0.84, "Movement1": 1, "AngleZ": 3,
                       "LForearm": 0.32, "LHand": 0.2}),
        (start + 1.24, {"Param36": high, "Movement1": 1, "AngleZ": 8, "LForearm": -0.22,
                        "LHand": -0.12}),
        (end - 0.25, {"Param36": high * 0.4, "Movement1": 1, "AngleZ": 3,
                      "LForearm": 0, "LHand": 0, "ParamMouthForm": 0.35}),
        (end, {"Param36": 0, "Movement1": 0, "AngleZ": 0, "AngleY": 0,
               "ParamMouthForm": 0.15, "CheeckPuff": 0}),
    ]


def procedural_clips() -> dict[str, dict]:
    clips: dict[str, dict] = {}

    clips["nod"] = _clip(
        1.35,
        [
            (0.0, {"AngleX": 0, "ParamAngleX": 0}),
            (0.24, {"AngleX": -10, "ParamAngleX": -4, "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7}),
            (0.52, {"AngleX": -1, "ParamAngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
            (0.8, {"AngleX": -9, "ParamAngleX": -3, "ParamEyeLOpen": 0.72, "ParamEyeROpen": 0.72}),
            (1.35, {"AngleX": 0, "ParamAngleX": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    clips["hop"] = _clip(
        0.95,
        [
            (0.0, {"AngleX": 0, "Param36": 0, "ParamBodyAngleX": 0}),
            (0.2, {"AngleX": 8, "Param36": 0.55, "ParamBodyAngleX": 4,
                   "ParamMouthForm": 0.5, "ParamEyeLOpen": 0.85, "ParamEyeROpen": 0.85}),
            (0.5, {"AngleX": -4, "Param36": 0.3, "ParamBodyAngleX": -2,
                   "ParamEyeLOpen": 0.6, "ParamEyeROpen": 0.6}),
            (0.75, {"AngleX": 2, "Param36": 0.1, "ParamBodyAngleX": 1}),
            (0.95, {"AngleX": 0, "Param36": 0, "ParamBodyAngleX": 0, "ParamMouthForm": 0.1,
                    "ParamEyeLOpen": 1, "ParamEyeROpen": 1}),
        ],
    )

    clips["shake"] = _clip(
        1.45,
        [
            (0.0, {"AngleY": 0, "ParamAngleY": 0}),
            (0.22, {"AngleY": 13, "ParamAngleY": 5, "AngleZ": -3}),
            (0.48, {"AngleY": -13, "ParamAngleY": -5, "AngleZ": 3}),
            (0.74, {"AngleY": 10, "ParamAngleY": 4, "AngleZ": -2}),
            (1.0, {"AngleY": -8, "ParamAngleY": -3, "AngleZ": 2}),
            (1.45, {"AngleY": 0, "ParamAngleY": 0, "AngleZ": 0}),
        ],
    )

    clips["tilt"] = _clip(
        2.8,
        [
            (0.0, {"AngleZ": 0, "CheeckPuff": 0}),
            (0.55, {"AngleZ": 16, "ParamAngleZ": 6, "AngleY": -4, "CheeckPuff": 0.35,
                    "ParamMouthForm": 0.55, "ParamEyeLOpen": 0.88, "ParamEyeROpen": 0.88}),
            (1.9, {"AngleZ": 15, "ParamAngleZ": 6, "AngleY": -4, "CheeckPuff": 0.32,
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
        + [(3.4, {"Param36": 0, "Movement1": 0, "AngleZ": 0, "AngleY": 0})],
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
            (0.0, {"Param36": 0, "Movement1": 0, "Movement2": 0}),
            (0.5, {"Param36": 1, "Movement1": 1, "Movement2": 0, "LArm": 0.35,
                   "LForearm": -0.3, "AngleX": 9, "AngleY": -4, "ParamMouthOpenY": 0.5,
                   "ParamEyeLOpen": 0.55, "ParamEyeROpen": 0.55, "ParamBodyAngleX": 4}),
            (1.3, {"Param36": 1, "LArm": 0.5, "LForearm": -0.45, "AngleX": 11,
                   "AngleZ": 4, "ParamMouthOpenY": 0.6}),
            (2.1, {"Param36": 1, "LArm": 0.28, "LForearm": -0.2, "AngleX": 7,
                   "AngleZ": -3, "ParamMouthOpenY": 0.35}),
            (2.8, {"Param36": 0.4, "LArm": 0.1, "LForearm": 0, "AngleX": 2,
                   "ParamMouthOpenY": 0.12, "ParamEyeLOpen": 0.85, "ParamEyeROpen": 0.85}),
            (3.6, {"Param36": 0, "Movement1": 0, "LArm": 0, "AngleX": 0, "AngleZ": 0,
                   "ParamMouthOpenY": 0, "ParamEyeLOpen": 1, "ParamEyeROpen": 1,
                   "ParamBodyAngleX": 0}),
        ],
    )

    clips["bow"] = _clip(
        2.9,
        [
            (0.0, {"AngleX": 0, "ParamBodyAngleX": 0}),
            (0.55, {"AngleX": -21, "ParamAngleX": -8, "ParamBodyAngleX": -13, "AngleY": 3,
                    "ParamEyeLOpen": 0.7, "ParamEyeROpen": 0.7, "ParamMouthForm": -0.1}),
            (1.8, {"AngleX": -20, "ParamAngleX": -8, "ParamBodyAngleX": -12, "AngleY": 3,
                   "ParamEyeLOpen": 0.68, "ParamEyeROpen": 0.68}),
            (2.9, {"AngleX": 0, "ParamAngleX": 0, "ParamBodyAngleX": 0, "AngleY": 0,
                   "ParamEyeLOpen": 1, "ParamEyeROpen": 1, "ParamMouthForm": 0.1}),
        ],
    )

    clips["dress"] = _clip(
        4.6,
        [
            (0.0, {"Clothes1": 1, "AngleZ": 0, "AngleY": 0}),
            (0.8, {"Clothes1": 0, "AngleZ": 12, "AngleY": 10, "ParamMouthForm": 0.55,
                   "ParamEyeLOpen": 0.9, "ParamEyeROpen": 0.9}),
            (1.5, {"Clothes1": 0, "AngleZ": -14, "AngleY": -12, "ParamBodyAngleZ": -7}),
            (2.2, {"Clothes1": 0, "AngleZ": 13, "AngleY": 11, "ParamBodyAngleZ": 6}),
            (2.9, {"Clothes1": 0, "AngleZ": -10, "AngleY": -8, "ParamBodyAngleZ": -4}),
            (3.6, {"Clothes1": 0, "AngleZ": 5, "AngleY": 4, "ParamBodyAngleZ": 2}),
            (4.0, {"Clothes1": 1, "AngleZ": 0, "AngleY": 0, "ParamBodyAngleZ": 0,
                   "ParamMouthForm": 0.2}),
            (4.6, {"Clothes1": 1, "AngleZ": 0, "AngleY": 0, "ParamMouthForm": 0}),
        ],
    )

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
            (3.5, {"Clothes2": 0, "AngleZ": 0, "AngleY": 0}),
            (3.9, {"Clothes2": 0, "ParamMouthForm": 0}),
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

    sing_frames = [(0.0, {"ParamMouthOpenY": 0, "ParamMouthForm": 0.3, "AngleZ": 0})]
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
                    "Param36": 0.18,
                },
            )
        )
    sing_frames.append((4.3, {"ParamMouthOpenY": 0, "ParamMouthForm": 0.1,
                              "AngleZ": 0, "AngleY": 0, "Param36": 0}))
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
                        "Movement1": 1 if side > 0 else 0,
                        "Movement2": 0 if side > 0 else 1,
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
                               "Param36": 0, "Movement1": 0, "Movement2": 0,
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

    clips["tail_hide"] = _clip(
        2.6,
        [
            (0.0, {"Clothes5": 1}),
            (0.4, {"Clothes5": -1, "AngleZ": 6, "ParamMouthForm": 0.4, "CheeckPuff": 0.2}),
            (1.8, {"Clothes5": -1, "AngleZ": -4, "CheeckPuff": 0.15}),
            (2.6, {"Clothes5": 1, "AngleZ": 0, "ParamMouthForm": 0.1, "CheeckPuff": 0}),
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

    return clips


def expression_clips(model_dir: Path) -> dict[str, dict]:
    """把 Exp3 里的表情转成常驻姿势。"""
    expressions: dict[str, dict] = {}
    root = Path(model_dir)
    if not root.is_dir():
        return expressions
    for path in sorted(root.rglob("*.exp3.json")):
        name = path.name[: -len(".exp3.json")]
        alias = EXPRESSION_ALIASES.get(name)
        if alias is None:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        params: dict[str, float] = {}
        for item in data.get("Parameters", []):
            if not isinstance(item, dict):
                continue
            pid = str(item.get("Id") or "").strip()
            if not pid or not pid.startswith(KEEP_PARAM_PREFIXES):
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


def motion_clip(path: Path, name: str) -> dict:
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
    return _clip(duration, ordered, loop=False)


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
                t1 = float(segments[index])
                v1 = float(segments[index + 1])
                c1t = float(segments[index + 2])
                c1v = float(segments[index + 3])
                c2t = float(segments[index + 4])
                c2v = float(segments[index + 5])
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
    """返回 (动作, 表情)。"""
    model_dir = Path(model_dir)
    clips = procedural_clips()
    if model_dir.is_dir():
        for path in model_dir.rglob("*.motion3.json"):
            alias = MOTION_ALIASES.get(path.name[: -len(".motion3.json")])
            if alias is None:
                continue
            try:
                clips[alias] = motion_clip(path, alias)
            except (OSError, ValueError, TypeError):
                continue
    expressions = expression_clips(model_dir)
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
