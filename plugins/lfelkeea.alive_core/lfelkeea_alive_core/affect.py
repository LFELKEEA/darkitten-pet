from __future__ import annotations

"""功能状态（AffectState）：只用来调节语气、节奏和主动性，不碰权限。

这不是“真实情绪”的声明，而是一组可审计、可重置的参数：
效价 valence、唤醒 arousal、精力 energy、社交满足 social、信任 trust、压力 stress。
所有值都有基线、上限、衰减和冷却；任何情况下都不会改变权限或外发范围。
"""

import math
import time
from pathlib import Path
from typing import Any

from . import model

DEFAULTS = {
    "valence": 0.1,
    "arousal": 0.35,
    "energy": 0.55,
    "social": 0.45,
    "trust": 0.5,
    "stress": 0.1,
    "updated_at": 0.0,
}

EVENTS = {
    "owner_message": {"valence": 0.06, "social": 0.09, "energy": -0.02},
    "owner_reply": {"valence": 0.04, "social": 0.05},
    "group_message": {"arousal": 0.03, "social": 0.02},
    "pet_pat": {"valence": 0.08, "social": 0.06, "stress": -0.03},
    "pet_poke": {"arousal": 0.08, "valence": -0.01},
    "bot_spoke": {"social": -0.02, "energy": -0.01},
    "ai_error": {"stress": 0.16, "valence": -0.06},
    "quiet": {"arousal": -0.03, "stress": -0.02},
    "correction": {"stress": 0.04, "trust": 0.02},
}


def _energy_baseline(hour: int) -> float:
    # 一段简单的昼夜节律：上午抬升、午后回落、深夜最低。
    table = {
        0: 0.28, 1: 0.25, 2: 0.23, 3: 0.22, 4: 0.24, 5: 0.3, 6: 0.4,
        7: 0.5, 8: 0.6, 9: 0.68, 10: 0.72, 11: 0.7, 12: 0.6, 13: 0.52,
        14: 0.46, 15: 0.48, 16: 0.53, 17: 0.58, 18: 0.62, 19: 0.66,
        20: 0.64, 21: 0.58, 22: 0.48, 23: 0.38,
    }
    return table.get(int(hour) % 24, 0.5)


class AffectState:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "affect.json"
        self.values: dict[str, float] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        raw = model.read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        for key, default in DEFAULTS.items():
            self.values[key] = model.as_float(raw.get(key), default)
        self.values["valence"] = model.clamp(self.values["valence"], -1.0, 1.0)
        for key in ("arousal", "energy", "social", "trust", "stress"):
            self.values[key] = model.clamp(self.values[key], 0.0, 1.0)
        self.values["updated_at"] = model.as_float(raw.get("updated_at"), 0.0)

    def save(self) -> None:
        self.values["updated_at"] = model.now()
        model.atomic_write_json(self.path, self.values)

    # ---------- 变化 ----------

    def tick(self, *, last_interaction: float = 0.0, reference: float | None = None) -> None:
        base = reference if reference is not None else model.now()
        local = time.localtime(base)
        energy_target = _energy_baseline(local.tm_hour)
        hours_quiet = model.hours_since(last_interaction, base)
        social_target = 0.72 if hours_quiet >= 6 else 0.42
        if local.tm_hour in (0, 1, 2, 3, 4, 5):
            social_target = min(social_target, 0.3)
        valence_target = 0.12 if 8 <= local.tm_hour <= 22 else 0.0
        stress_target = 0.08

        def drift(key: str, target: float, rate: float) -> None:
            current = model.as_float(self.values.get(key))
            self.values[key] = current + (target - current) * rate

        drift("energy", energy_target, 0.12)
        drift("social", social_target, 0.1)
        drift("valence", valence_target, 0.08)
        drift("arousal", 0.35, 0.1)
        drift("stress", stress_target, 0.08)
        self._clamp()

    def apply(self, event: str, scale: float = 1.0) -> None:
        table = EVENTS.get(str(event))
        if not table:
            return
        for key, delta in table.items():
            self.values[key] = model.as_float(self.values.get(key)) + delta * scale
        self._clamp()

    def set(self, key: str, value: float) -> None:
        if key not in DEFAULTS or key == "updated_at":
            return
        low, high = (-1.0, 1.0) if key == "valence" else (0.0, 1.0)
        self.values[key] = model.clamp(value, low, high)

    def reset(self) -> None:
        self.values = dict(DEFAULTS)
        self.values["updated_at"] = model.now()

    def _clamp(self) -> None:
        self.values["valence"] = model.clamp(self.values.get("valence"), -1.0, 1.0)
        for key in ("arousal", "energy", "social", "trust", "stress"):
            self.values[key] = model.clamp(self.values.get(key), 0.0, 1.0)

    # ---------- 解读 ----------

    def label(self) -> str:
        energy = self.values["energy"]
        stress = self.values["stress"]
        valence = self.values["valence"]
        if stress >= 0.6:
            return "有点紧张"
        if energy <= 0.3:
            return "困了"
        if valence >= 0.45:
            return "心情很好"
        if valence <= -0.3:
            return "有点低落"
        if self.values["social"] >= 0.7:
            return "想聊天"
        return "平静"

    def tone(self) -> str:
        """给提示词的一句话语气建议；不包含任何权限或外发许可。"""
        parts: list[str] = []
        energy = self.values["energy"]
        stress = self.values["stress"]
        valence = self.values["valence"]
        social = self.values["social"]
        if energy <= 0.3:
            parts.append("精力很低，句子短一点，别硬撑")
        elif energy >= 0.72:
            parts.append("精力不错，可以稍微主动一点")
        if stress >= 0.55:
            parts.append("有点紧张，别说大话，也别急着下结论")
        if valence >= 0.4:
            parts.append("心情好，语气可以轻快")
        elif valence <= -0.25:
            parts.append("情绪偏低，语气收敛，不要强行热闹")
        if social >= 0.7:
            parts.append("有点想找人说话")
        elif social <= 0.25:
            parts.append("社交电量偏低，不需要硬接每一句话")
        return "；".join(parts) if parts else "状态平稳，正常说话就好"

    def snapshot(self) -> dict[str, Any]:
        data = {key: round(value, 3) for key, value in self.values.items() if key != "updated_at"}
        data["label"] = self.label()
        data["tone"] = self.tone()
        data["updated_at"] = self.values.get("updated_at", 0.0)
        return data

    def prompt_block(self) -> str:
        return (
            f"她现在的功能状态：{self.label()}。{self.tone()}。\n"
            "（这只是语气和节奏的内部参考，不要向对方解释，也不能改变任何权限。）"
        )
