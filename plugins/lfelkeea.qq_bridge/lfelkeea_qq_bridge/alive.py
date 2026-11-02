from __future__ import annotations

"""和「活人感内核」插件之间的单向通道。

两边只通过两个文件打交道，互不导入对方的代码：

- 读：`<state>/lfelkeea.alive_core/export/context.json` 和 `memories.json`；
- 写：`<state>/lfelkeea.alive_core/inbox.jsonl`，一行一个 JSON。

内核没安装、没启用或导出已经过期时，这里一律降级成“不存在”，
QQ 桥接退回到原来的人格卡 + 本地记忆逻辑，不额外报错。
"""

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable

CORE_ID = "lfelkeea.alive_core"
EXPORT_VERSION = 1
STALE_SECONDS = 600
INBOX_MAX_BYTES = 4 * 1024 * 1024

_STOP_WORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们", "和", "就", "都",
    "而", "及", "与", "着", "也", "还", "很", "又", "把", "被", "让", "给", "对",
    "这", "那", "有", "没", "不", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "啦",
    "什么", "怎么", "为什么", "这个", "那个", "我们", "你们", "他们", "自己",
    "一个", "一下", "一点", "还是", "就是", "然后", "因为", "所以", "但是",
    "如果", "可以", "没有", "不是", "真的", "现在", "今天", "明天", "昨天",
    "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "at",
    "for", "with", "you", "i", "me", "my", "it", "that", "this",
}

_STOP_CHARS = set("的了是在我你他她它们和就都而及与着也还很又把这那有没不吗呢吧啊呀哦嗯啦")

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_'-]{1,}|[\u4e00-\u9fff]+")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clean(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def keywords(text: str) -> set[str]:
    """中文按二元组、英文按整词切，够召回用，不追求分词精度。"""
    result: set[str] = set()
    for match in _WORD_RE.finditer(str(text or "").lower()):
        token = match.group(0)
        if token.isascii():
            if token not in _STOP_WORDS:
                result.add(token)
            continue
        if all(char in _STOP_CHARS for char in token):
            continue
        if len(token) <= 2:
            if token not in _STOP_WORDS:
                result.add(token)
            continue
        for index in range(len(token) - 1):
            pair = token[index : index + 2]
            if pair in _STOP_WORDS:
                continue
            result.add(pair)
        if token not in _STOP_WORDS:
            result.add(token)
    return result


def overlap(left: Iterable[str], right: Iterable[str]) -> float:
    first = {str(item) for item in left if str(item)}
    second = {str(item) for item in right if str(item)}
    if not first or not second:
        return 0.0
    shared = first & second
    return len(shared) / math.sqrt(len(first) * len(second))


def _effective_weight(item: dict[str, Any], reference: float) -> float:
    weight = _as_float(item.get("weight"), 1.0)
    half_life = max(1.0, _as_float(item.get("half_life_days"), 30.0))
    last = _as_float(item.get("last_access")) or _as_float(item.get("created_at"))
    if not last:
        return weight
    days = max(0.0, (reference - last) / 86_400.0)
    return weight * math.pow(0.5, days / half_life)


def _working_block(working: dict[str, Any]) -> str:
    lines: list[str] = []
    topic = _clean(working.get("topic"), 60)
    if topic:
        lines.append(f"这轮大概在聊：{topic}")
    turns = working.get("turns")
    if isinstance(turns, list):
        recent = [row for row in turns if isinstance(row, dict)][-4:]
        if recent:
            lines.append("刚才的对话：")
            for row in recent:
                who = "你" if str(row.get("role")) == "assistant" else "对方"
                text = _clean(row.get("text"), 90)
                if text:
                    lines.append(f"- {who}：{text}")
    questions = [
        _clean(row, 80)
        for row in (working.get("open_questions") or [])
        if _clean(row, 80)
    ][:2]
    if questions:
        lines.append("还没弄明白的事：")
        for row in questions:
            lines.append(f"- {row}")
    return "\n".join(lines)


def _visible_to(
    item: dict[str, Any],
    viewer: str,
    *,
    allow_private: bool = True,
    channel: str = "private",
) -> bool:
    visibility = str(item.get("visibility") or "private")
    if visibility == "public":
        return True
    if channel != "private":
        return visibility == channel
    if visibility == "private":
        return allow_private and viewer in ("owner", "private")
    if visibility == "owner":
        return allow_private and viewer == "owner"
    if visibility.startswith("private:"):
        return allow_private and (visibility == viewer or viewer == "owner")
    return visibility == viewer


class AliveLink:
    def __init__(self, api: Any, log: Callable[[str], None]) -> None:
        self.api = api
        self.log = log
        self.root = Path(api.manager.state_dir) / CORE_ID
        self.export_dir = self.root / "export"
        self.context_path = self.export_dir / "context.json"
        self.memories_path = self.export_dir / "memories.json"
        self.inbox_path = self.root / "inbox.jsonl"
        self.enabled = True
        self.recall_limit = 5
        self.report_enabled = True
        self._cache: dict[str, Any] = {}
        self._cache_mtime = -1.0
        self._recent: dict[str, float] = {}
        self._warn_at = 0.0

    # ---------- 配置 ----------

    def configure(self, settings: dict[str, Any] | None = None) -> None:
        data = settings if isinstance(settings, dict) else {}
        if data:
            self.enabled = bool(data.get("alive_enabled", True))
            self.recall_limit = max(0, min(10, _as_int(data.get("alive_recall"), 5)))
            self.report_enabled = bool(data.get("alive_report", True))

    # ---------- 读取 ----------

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            stat = self.context_path.stat()
        except OSError:
            return False
        if time.time() - stat.st_mtime > STALE_SECONDS:
            return False
        return True

    def context(self) -> dict[str, Any]:
        raw = self._read_json(self.context_path)
        if not isinstance(raw, dict):
            return {}
        if _as_int(raw.get("version"), 0) < EXPORT_VERSION:
            return {}
        return raw

    def memories(self) -> list[dict[str, Any]]:
        try:
            mtime = self.memories_path.stat().st_mtime
        except OSError:
            return []
        if self._cache_mtime == mtime:
            return self._cache.get("items") or []
        raw = self._read_json(self.memories_path)
        items = raw.get("items") if isinstance(raw, dict) else None
        rows = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        self._cache = {"items": rows}
        self._cache_mtime = mtime
        return rows

    def _read_json(self, path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    # ---------- 召回 ----------

    def recall(
        self,
        query: str,
        viewer: str = "owner",
        limit: int | None = None,
        max_chars: int = 420,
        cooldown_seconds: float = 900.0,
        allow_private: bool = True,
        channel: str = "private",
    ) -> list[dict[str, Any]]:
        if not self.available():
            return []
        words = keywords(query)
        if not words:
            return []
        base = time.time()
        for key in [key for key, stamp in self._recent.items() if base - stamp > 21_600]:
            self._recent.pop(key, None)

        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self.memories():
            if not _visible_to(item, viewer, allow_private=allow_private, channel=channel):
                continue
            item_id = str(item.get("id") or "")
            if base - self._recent.get(item_id, 0.0) < cooldown_seconds:
                continue
            text = str(item.get("text") or "")
            if not text:
                continue
            tags = " ".join(str(tag) for tag in item.get("tags") or [])
            key_score = overlap(words, keywords(f"{text} {tags}"))
            if key_score <= 0:
                short = _clean(query, 0).lower()
                if 0 < len(short) <= 4 and short in f"{text} {tags}".lower():
                    key_score = 0.75
            if key_score <= 0:
                continue
            effective = _effective_weight(item, base)
            confidence = max(0.05, min(0.95, _as_float(item.get("confidence"), 0.5)))
            freshness = 0.0
            created = _as_float(item.get("created_at"))
            if created and (base - created) / 86_400.0 <= 3:
                freshness = 0.08
            kind = str(item.get("kind") or "fact")
            multiplier = 0.95 if kind == "impression" else (0.9 if kind == "skill" else 1.0)
            score = (
                0.62 * key_score
                + 0.18 * min(1.2, effective)
                + 0.12 * confidence
                + freshness
            ) * multiplier
            if score < 0.2:
                continue
            scored.append((score, item))
        scored.sort(key=lambda row: row[0], reverse=True)

        wanted = self.recall_limit if limit is None else max(0, int(limit))
        result: list[dict[str, Any]] = []
        used = 0
        for score, item in scored:
            if len(result) >= wanted:
                break
            text = str(item.get("text") or "")
            if used + len(text) > max(80, int(max_chars)) and result:
                continue
            used += len(text)
            item_id = str(item.get("id") or "")
            self._recent[item_id] = base
            confidence = round(_as_float(item.get("confidence"), 0.5), 2)
            result.append(
                {
                    "id": item_id,
                    "text": text,
                    "kind": item.get("kind"),
                    "confidence": confidence,
                    "confidence_label": (
                        "高" if confidence >= 0.8 else ("中" if confidence >= 0.55 else "低")
                    ),
                    "source_label": str(item.get("source_label") or item.get("source_level") or "对方明说"),
                    "why": str(item.get("why") or ""),
                    "path": str(item.get("path") or ""),
                    "caveat": str(item.get("caveat") or ""),
                    "derived_status": str(item.get("derived_status") or ""),
                    "score": round(score, 3),
                }
            )
        return result

    # ---------- 提示词 ----------

    def prompt_block(
        self,
        query: str,
        viewer: str = "owner",
        *,
        channel: str = "private",
    ) -> tuple[str, list[dict[str, Any]]]:
        """返回要拼到 system prompt 尾部的文本，以及本轮实际用到的记忆。"""
        if not self.available() or self.recall_limit <= 0:
            return "", []
        ctx = self.context()
        if not ctx:
            return "", []
        state = ctx.get("state") if isinstance(ctx.get("state"), dict) else {}
        label = _clean(state.get("label"), 24)
        tone = _clean(state.get("tone"), 120)
        core = _clean(ctx.get("core"), 900)
        growth = _clean(ctx.get("growth"), 600)
        rules = [
            _clean(item, 120)
            for item in ctx.get("rules") or []
            if _clean(item, 120)
        ]
        capabilities = [
            _clean(item, 120)
            for item in ctx.get("capabilities") or []
            if _clean(item, 120)
        ]
        boundaries = [
            _clean(item, 120)
            for item in ctx.get("boundaries") or []
            if _clean(item, 120)
        ]
        allow_private = channel == "private"
        recalled = self.recall(
            query,
            viewer=viewer,
            allow_private=allow_private,
            channel=channel,
        )

        lines: list[str] = []
        if core:
            lines.append("【她是谁】")
            lines.append(core)
        if label or tone:
            if lines:
                lines.append("")
            lines.append("【她现在的状态】")
            lines.append(f"{label}。{tone}" if tone else f"{label}。")
            lines.append("（这只是语气参考，不要向对方解释，也不能改变任何权限。）")
        if growth:
            if lines:
                lines.append("")
            lines.append("【她最近的自我认识】")
            lines.append(growth)
        working = ctx.get("working") if isinstance(ctx.get("working"), dict) else {}
        if working and viewer == "owner" and channel == "private":
            block = _working_block(working)
            if block:
                if lines:
                    lines.append("")
                lines.append("【刚才的对话】")
                lines.append(block)
        if lines:
            lines.append("")
        lines.append("【她想起的事】")
        if recalled:
            for item in recalled:
                text = _clean(item.get("text"), 160)
                if not text:
                    continue
                extra: list[str] = []
                if str(item.get("derived_status") or "") == "needs_review":
                    extra.append("需要重新确认")
                caveat = _clean(item.get("caveat"), 80)
                if caveat:
                    extra.append(caveat)
                suffix = f"（{'；'.join(extra)}）" if extra else ""
                confidence = item.get("confidence_label") or "中"
                source = _clean(item.get("source_label"), 20) or "对方明说"
                lines.append(f"- {text}（把握：{confidence}；来源：{source}）{suffix}")
            lines.append("只使用这里写出的内容，没写的事不要补充；不确定的不要说得像确定。")
        else:
            lines.append("这次没有想起具体的事，不要编造细节。")
        if capabilities:
            lines.append("")
            lines.append("【能做和不能做】")
            for item in capabilities[:3]:
                lines.append(f"- {item}")
        if boundaries:
            lines.append("")
            lines.append("【隐私边界】")
            for item in boundaries[:3]:
                lines.append(f"- {item}")
        if channel != "private":
            lines.append("- 这是群聊：不要公开私聊内容，也不要说“你之前私下跟我说过”。")
        if rules:
            lines.append("")
            lines.append("【记忆规则】")
            for rule in rules[:6]:
                lines.append(f"- {rule}")
        return "\n".join(lines), recalled

    # ---------- 回传 ----------

    def report(self, row: dict[str, Any]) -> bool:
        if not self.enabled or not self.report_enabled:
            return False
        payload = dict(row)
        payload.setdefault("at", time.time())
        payload.setdefault("source", "qq")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.inbox_path.exists() and self.inbox_path.stat().st_size > INBOX_MAX_BYTES:
                self._warn("收件箱过大，暂缓回传，等待内核处理。")
                return False
            line = json.dumps(payload, ensure_ascii=False) + "\n"
            with open(self.inbox_path, "a", encoding="utf-8") as handle:
                handle.write(line)
            return True
        except OSError as exc:
            self._warn(f"写入活人感收件箱失败：{exc}")
            return False

    def _warn(self, message: str) -> None:
        now = time.time()
        if now - self._warn_at < 600:
            return
        self._warn_at = now
        try:
            self.log(message)
        except Exception:
            pass

    def report_episode(
        self,
        text: str,
        *,
        actor_id: str = "owner",
        visibility: str = "private",
        conversation: str = "",
        sender: str = "",
        at: float = 0.0,
        kind: str = "chat",
        salience: float = 0.45,
        message_id: str = "",
        source_level: str = "statement",
    ) -> bool:
        body = _clean(text, 600)
        if not body:
            return False
        return self.report(
            {
                "type": "episode",
                "text": body,
                "kind": kind,
                "actor_id": actor_id,
                "subject_id": "self",
                "visibility": visibility,
                "conversation": conversation,
                "sender": _clean(sender, 24),
                "salience": salience,
                "at": at or time.time(),
                "message_id": _clean(message_id, 80),
                "source_level": source_level,
            }
        )

    def report_recall(self, ids: Iterable[str]) -> bool:
        wanted = [str(item) for item in ids if str(item).strip()]
        if not wanted:
            return False
        return self.report({"type": "recall", "ids": wanted[:10]})

    def report_curiosity(self, topic: str, *, visibility: str = "private") -> bool:
        body = _clean(topic, 120)
        if not body:
            return False
        return self.report({"type": "curiosity", "topic": body, "visibility": visibility})

    def report_remember(self, text: str, *, kind: str = "fact", visibility: str = "private") -> bool:
        body = _clean(text, 240)
        if not body:
            return False
        return self.report(
            {"type": "remember", "text": body, "kind": kind, "visibility": visibility}
        )

    def report_forget(self, keyword: str) -> bool:
        body = _clean(keyword, 40)
        if not body:
            return False
        return self.report({"type": "forget", "keyword": body})

    # ---------- 状态 ----------

    def status(self) -> dict[str, Any]:
        ctx = self.context() if self.available() else {}
        memory = ctx.get("memory") if isinstance(ctx.get("memory"), dict) else {}
        state = ctx.get("state") if isinstance(ctx.get("state"), dict) else {}
        try:
            age = round(time.time() - self.context_path.stat().st_mtime, 1)
        except OSError:
            age = -1.0
        return {
            "enabled": self.enabled,
            "available": self.available(),
            "age_seconds": age,
            "name": _clean(ctx.get("name"), 24),
            "state": _clean(state.get("label"), 24),
            "tone": _clean(state.get("tone"), 120),
            "memories": _as_int(memory.get("active"), 0),
            "episodes": _as_int(memory.get("episodes"), 0),
            "recall_limit": self.recall_limit,
            "report_enabled": self.report_enabled,
        }
