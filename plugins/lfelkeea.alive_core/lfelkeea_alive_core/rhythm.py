from __future__ import annotations

"""节律与主动预算：有信号才醒，额度用完就停，夜间默认静默。

主动不等于刷屏。这里只记录“今天还能主动几次”“同类信号多久内不重复”，
是否真的有话可说由调用方决定；允许一整天都没有输出。
"""

from pathlib import Path
from typing import Any

from . import model


class Rhythm:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "rhythm.json"
        self.state: dict[str, Any] = {
            "day": "",
            "count": 0,
            "total": 0,
            "last_at": 0.0,
            "signals": {},
            "topics": {},
        }
        self.load()

    def load(self) -> None:
        raw = model.read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        self.state = {
            "day": str(raw.get("day") or ""),
            "count": model.as_int(raw.get("count")),
            "total": model.as_int(raw.get("total")),
            "last_at": model.as_float(raw.get("last_at")),
            "signals": raw.get("signals") if isinstance(raw.get("signals"), dict) else {},
            "topics": raw.get("topics") if isinstance(raw.get("topics"), dict) else {},
        }

    def save(self) -> None:
        model.atomic_write_json(self.path, self.state)

    def _roll(self, reference: float) -> None:
        key = model.day_key(reference)
        if self.state.get("day") != key:
            self.state["day"] = key
            self.state["count"] = 0

    def quiet(self, start: str, end: str, reference: float | None = None) -> bool:
        return model.in_quiet_hours(reference if reference is not None else model.now(), start, end)

    def budget_left(self, limit: int, reference: float | None = None) -> int:
        base = reference if reference is not None else model.now()
        self._roll(base)
        return max(0, int(limit) - model.as_int(self.state.get("count")))

    def can_speak(
        self,
        *,
        limit: int,
        min_interval_minutes: int,
        reference: float | None = None,
    ) -> tuple[bool, str]:
        base = reference if reference is not None else model.now()
        self._roll(base)
        if self.budget_left(limit, base) <= 0:
            return False, "今天的主动额度用完了"
        gap = (base - model.as_float(self.state.get("last_at"))) / 60.0
        if model.as_float(self.state.get("last_at")) and gap < max(5, int(min_interval_minutes)):
            return False, "离上一次主动太近了"
        return True, ""

    def mark_spoken(self, reference: float | None = None) -> None:
        base = reference if reference is not None else model.now()
        self._roll(base)
        self.state["count"] = model.as_int(self.state.get("count")) + 1
        self.state["total"] = model.as_int(self.state.get("total")) + 1
        self.state["last_at"] = base
        self.save()

    def signal_due(self, name: str, cooldown_hours: float, reference: float | None = None) -> bool:
        base = reference if reference is not None else model.now()
        signals = self.state.setdefault("signals", {})
        last = model.as_float(signals.get(name))
        return base - last >= max(1.0, cooldown_hours) * 3600

    def mark_signal(self, name: str, reference: float | None = None) -> None:
        base = reference if reference is not None else model.now()
        self.state.setdefault("signals", {})[name] = base
        self.save()

    def topic_seen(self, topic: str, cooldown_hours: float, reference: float | None = None) -> bool:
        base = reference if reference is not None else model.now()
        key = model.clean_text(topic, 40).lower()
        if not key:
            return True
        topics = self.state.setdefault("topics", {})
        return base - model.as_float(topics.get(key)) < max(1.0, cooldown_hours) * 3600

    def mark_topic(self, topic: str, reference: float | None = None) -> None:
        base = reference if reference is not None else model.now()
        key = model.clean_text(topic, 40).lower()
        if not key:
            return
        topics = self.state.setdefault("topics", {})
        topics[key] = base
        if len(topics) > 80:
            keep = sorted(topics.items(), key=lambda row: model.as_float(row[1]), reverse=True)[:60]
            self.state["topics"] = dict(keep)
        self.save()

    def snapshot(self, limit: int, reference: float | None = None) -> dict[str, Any]:
        base = reference if reference is not None else model.now()
        self._roll(base)
        return {
            "day": self.state.get("day"),
            "count": model.as_int(self.state.get("count")),
            "limit": int(limit),
            "left": self.budget_left(limit, base),
            "last_at": model.as_float(self.state.get("last_at")),
            "total": model.as_int(self.state.get("total")),
        }
