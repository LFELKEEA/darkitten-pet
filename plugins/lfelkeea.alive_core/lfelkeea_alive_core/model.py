from __future__ import annotations

"""活人感内核的公共小工具：时间、数值、文本、关键词和原子写盘。

这里只做纯计算，不访问网络，也不依赖 Qt，方便单独测试。
"""

import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

DAY = 86_400.0

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_LATIN = re.compile(r"[a-zA-Z0-9_]+")
_SPACE = re.compile(r"\s+")

# 这些词几乎出现在每句话里，参与打分只会让召回变吵。
STOP_WORDS = {
    "这个", "那个", "什么", "怎么", "为什", "可以", "没有", "就是", "还是", "但是",
    "因为", "所以", "如果", "然后", "现在", "今天", "明天", "昨天", "时候", "自己",
    "我们", "你们", "他们", "她们", "一个", "一下", "一点", "真的", "不是", "这样",
    "那样", "觉得", "知道", "感觉", "可能", "应该", "已经", "还有", "而且", "或者",
    "the", "and", "for", "you", "are", "was", "were", "that", "this", "with", "have",
    "has", "had", "not", "but", "what", "when", "where", "how", "why", "can", "will",
}


def now() -> float:
    return time.time()


def clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = low
    if math.isnan(number):
        return low
    return max(low, min(high, number))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y", "是", "开"}:
        return True
    if text in {"0", "false", "no", "off", "n", "否", "关", ""}:
        return False
    return default


def new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000):x}{uuid.uuid4().hex[:6]}"


def clean_text(value: Any, limit: int = 400) -> str:
    text = _SPACE.sub(" ", str(value or "")).strip()
    if limit > 0 and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def hash_text(text: str) -> str:
    return hashlib.sha1(clean_text(text, 0).lower().encode("utf-8")).hexdigest()[:16]


def keywords(text: Any) -> set[str]:
    """中文取二元组，英文取整词；过短的片段不参与匹配。"""
    value = clean_text(text, 0).lower()
    result: set[str] = set()
    for run in _CJK.findall(value):
        if len(run) == 1:
            result.add(run)
            continue
        for index in range(len(run) - 1):
            token = run[index : index + 2]
            if token not in STOP_WORDS:
                result.add(token)
    for token in _LATIN.findall(value):
        if len(token) >= 2 and token not in STOP_WORDS:
            result.add(token)
    return result


def overlap(query: Iterable[str], target: Iterable[str]) -> float:
    """查询词有多大比例被目标覆盖；目标越长越不占便宜。"""
    left = set(query or ())
    right = set(target or ())
    if not left or not right:
        return 0.0
    hit = len(left & right)
    if not hit:
        return 0.0
    return hit / max(1, len(left)) * 0.75 + hit / max(1, len(right)) * 0.25


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def parse_clock(value: Any, default: str = "00:00") -> tuple[int, int]:
    match = re.match(r"^\s*(\d{1,2})[:：](\d{1,2})\s*$", str(value or ""))
    if match is None:
        match = re.match(r"^\s*(\d{1,2})[:：](\d{1,2})\s*$", default)
        if match is None:
            return 0, 0
    hour = max(0, min(23, int(match.group(1))))
    minute = max(0, min(59, int(match.group(2))))
    return hour, minute


def in_quiet_hours(ts: float, start: str = "23:30", end: str = "07:30") -> bool:
    start_h, start_m = parse_clock(start, "23:30")
    end_h, end_m = parse_clock(end, "07:30")
    local = time.localtime(ts)
    current = local.tm_hour * 60 + local.tm_min
    first = start_h * 60 + start_m
    last = end_h * 60 + end_m
    if first == last:
        return False
    if first < last:
        return first <= current < last
    return current >= first or current < last


def hours_since(ts: float, reference: float | None = None) -> float:
    base = reference if reference is not None else now()
    if not ts:
        return 9999.0
    return max(0.0, (base - float(ts)) / 3600.0)


def age_days(ts: float, reference: float | None = None) -> float:
    base = reference if reference is not None else now()
    if not ts:
        return 9999.0
    return max(0.0, (base - float(ts)) / DAY)


def day_key(ts: float | None = None) -> str:
    """以凌晨 4 点为分界，避免熬夜时“今天”在零点被重置。"""
    value = time.localtime((ts if ts is not None else now()) - 4 * 3600)
    return time.strftime("%Y-%m-%d", value)


def human_day(ts: float) -> str:
    if not ts:
        return "更早"
    days = age_days(ts)
    if days < 1:
        return "今天"
    if days < 2:
        return "昨天"
    if days < 8:
        return f"{int(days)} 天前"
    if days < 60:
        return f"{int(days / 7)} 周前"
    return f"{int(days / 30)} 个月前"


def confidence_label(value: float) -> str:
    if value >= 0.8:
        return "高"
    if value >= 0.55:
        return "中"
    return "低"
