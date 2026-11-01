from __future__ import annotations

"""五层记忆的本地实现：情节、印象、事实、偏好、技能和元记忆。

设计取舍：
- 原文只追加，驱逐前先归档到 JSONL，不直接删除；
- 召回按“关键词相关 + 权重 + 可信度 + 新鲜度”打分，不每轮全量倒；
- 被真正用过的记忆才加固（reinvigorate），避免随机刷新 accessCount；
- 更正不覆盖历史，旧条目转为 superseded，新条目保留证据链；
- 巩固只产出候选，技能升级必须有真实证据。
"""

import json
import math
from pathlib import Path
from typing import Any, Iterable

from . import model

KINDS = ("impression", "fact", "preference", "skill", "curiosity")
LEVELS = ("aware", "learned", "practiced", "verified")
LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS)}

MAX_EPISODES = 4000
ARCHIVE_KEEP_DAYS = 3


def _score_entry(
    entry: dict[str, Any],
    query_words: set[str],
    reference: float,
    query_text: str = "",
) -> tuple[float, float, float]:
    text = str(entry.get("text", ""))
    tags = " ".join(str(tag) for tag in entry.get("tags") or [])
    key_score = model.overlap(query_words, model.keywords(f"{text} {tags}"))
    if key_score <= 0 and query_text:
        # 一两个字的短查询（“猫”“生日”）没有二元组，退化成直接包含判断。
        short = model.clean_text(query_text, 0).lower()
        if 0 < len(short) <= 4 and short in f"{text} {tags}".lower():
            key_score = 0.75
    if key_score <= 0:
        return 0.0, 0.0, 0.0
    effective = effective_weight(entry, reference)
    confidence = model.clamp(entry.get("confidence", 0.5))
    bonus = 0.0
    created = model.as_float(entry.get("created_at"))
    if created and model.age_days(created, reference) <= 3:
        bonus = 0.08
    kind = str(entry.get("kind", "impression"))
    multiplier = 1.0
    if kind == "impression":
        multiplier = 0.95
    elif kind == "skill":
        multiplier = 0.9
    score = (0.62 * key_score + 0.18 * min(1.2, effective) + 0.12 * confidence + bonus) * multiplier
    return score, key_score, effective


def effective_weight(entry: dict[str, Any], reference: float | None = None) -> float:
    """按遗忘曲线折算当前权重：没被回忆过的内容会慢慢变淡。"""
    weight = model.as_float(entry.get("weight"), 1.0)
    half_life = max(1.0, model.as_float(entry.get("half_life_days"), 30.0))
    last = model.as_float(entry.get("last_access")) or model.as_float(entry.get("created_at"))
    if not last:
        return weight
    days = model.age_days(last, reference)
    return weight * math.pow(0.5, days / half_life)


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / "memory.json"
        self.archive_dir = self.root / "archive"
        self.data: dict[str, Any] = {"version": 1, "episodes": [], "items": [], "meta": {}}
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        raw = model.read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}
        episodes = raw.get("episodes")
        items = raw.get("items")
        meta = raw.get("meta")
        self.data = {
            "version": 1,
            "episodes": [item for item in episodes if isinstance(item, dict)] if isinstance(episodes, list) else [],
            "items": [item for item in items if isinstance(item, dict)] if isinstance(items, list) else [],
            "meta": meta if isinstance(meta, dict) else {},
        }

    def save(self) -> None:
        model.atomic_write_json(self.path, self.data)

    # ---------- 情节记忆 ----------

    def add_episode(
        self,
        text: str,
        *,
        kind: str = "chat",
        actor_id: str = "owner",
        subject_id: str = "self",
        visibility: str = "private",
        source: str = "",
        tags: Iterable[str] | None = None,
        salience: float = 0.5,
        at: float | None = None,
        digest: str = "",
    ) -> dict[str, Any]:
        body = model.clean_text(text, 600)
        if not body:
            return {}
        stamp = model.as_float(at) or model.now()
        fingerprint = model.hash_text(f"{digest}|{body}")
        for existing in reversed(self.data["episodes"][-50:]):
            if (
                existing.get("fingerprint") == fingerprint
                and abs(model.as_float(existing.get("at")) - stamp) < 120
            ):
                return existing
        item = {
            "id": model.new_id("e"),
            "at": stamp,
            "kind": str(kind or "chat")[:24],
            "actor_id": str(actor_id or "owner")[:48],
            "subject_id": str(subject_id or "self")[:48],
            "visibility": str(visibility or "private")[:48],
            "text": body,
            "source": str(source or "")[:32],
            "tags": [model.clean_text(tag, 16) for tag in (tags or []) if model.clean_text(tag, 16)][:8],
            "salience": model.clamp(salience, 0.0, 1.0),
            "fingerprint": fingerprint,
        }
        self.data["episodes"].append(item)
        self._trim_episodes()
        return item

    def _trim_episodes(self) -> None:
        limit = max(200, model.as_int(self.data.get("meta", {}).get("max_episodes"), MAX_EPISODES))
        overflow = len(self.data["episodes"]) - limit
        if overflow <= 0:
            return
        moved = self.data["episodes"][:overflow]
        self.data["episodes"] = self.data["episodes"][overflow:]
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        stamp = model.time.strftime("%Y%m", model.time.localtime())
        target = self.archive_dir / f"episodes-{stamp}.jsonl"
        try:
            with open(target, "a", encoding="utf-8") as handle:
                for item in moved:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        except OSError:
            # 归档失败就放回去，宁可多占一点空间，也不丢原文。
            self.data["episodes"] = moved + self.data["episodes"]

    def episodes(self, limit: int = 0) -> list[dict[str, Any]]:
        rows = list(self.data["episodes"])
        return rows[-limit:] if limit else rows

    def episode_ids(self) -> set[str]:
        return {str(item.get("id")) for item in self.data["episodes"]}

    # ---------- 长期条目 ----------

    def items(
        self,
        *,
        kind: str | None = None,
        status: str = "active",
        visibility: str | None = None,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.data["items"]:
            if status and item.get("status", "active") != status:
                continue
            if kind and item.get("kind") != kind:
                continue
            if visibility is not None and item.get("visibility", "private") != visibility:
                continue
            rows.append(item)
        return rows

    def find(self, item_id: str) -> dict[str, Any] | None:
        for item in self.data["items"]:
            if item.get("id") == item_id:
                return item
        return None

    def add_item(
        self,
        text: str,
        *,
        kind: str = "fact",
        confidence: float = 0.6,
        weight: float = 1.0,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        visibility: str = "private",
        source: str = "manual",
        half_life_days: float = 30.0,
        level: str = "",
        at: float | None = None,
    ) -> dict[str, Any]:
        body = model.clean_text(text, 240)
        if not body:
            return {}
        valid_kind = kind if kind in KINDS else "fact"
        stamp = model.as_float(at) or model.now()
        merged = self._find_similar(body, valid_kind)
        if merged is not None:
            merged["weight"] = min(1.6, model.as_float(merged.get("weight"), 1.0) + 0.1)
            merged["confidence"] = min(0.95, max(model.as_float(merged.get("confidence"), 0.5), model.clamp(confidence)))
            merged["last_access"] = stamp
            merged["access_count"] = model.as_int(merged.get("access_count")) + 1
            self._merge_list(merged, "tags", tags)
            self._merge_list(merged, "evidence", evidence)
            merged["updated_at"] = stamp
            return merged
        item = {
            "id": model.new_id("m"),
            "text": body,
            "kind": valid_kind,
            "status": "active",
            "confidence": model.clamp(confidence, 0.05, 0.95),
            "weight": model.clamp(weight, 0.05, 1.6),
            "half_life_days": max(1.0, min(365.0, model.as_float(half_life_days, 30.0))),
            "created_at": stamp,
            "updated_at": stamp,
            "last_access": stamp,
            "access_count": 0,
            "tags": [model.clean_text(tag, 16) for tag in (tags or []) if model.clean_text(tag, 16)][:10],
            "evidence": [str(eid)[:64] for eid in (evidence or []) if str(eid).strip()][:20],
            "visibility": str(visibility or "private")[:48],
            "source": str(source or "manual")[:40],
            "superseded_by": "",
            "superseded_at": 0.0,
            "level": level if level in LEVELS else "",
            "locked": False,
        }
        if item["kind"] == "skill" and not item["level"]:
            item["level"] = "aware"
        self.data["items"].append(item)
        return item

    def _find_similar(self, text: str, kind: str) -> dict[str, Any] | None:
        words = model.keywords(text)
        if not words:
            return None
        for item in self.data["items"]:
            if item.get("status", "active") != "active":
                continue
            if item.get("kind") != kind:
                continue
            if model.hash_text(item.get("text", "")) == model.hash_text(text):
                return item
            score = model.overlap(words, model.keywords(item.get("text", "")))
            if score >= 0.9:
                return item
        return None

    @staticmethod
    def _merge_list(item: dict[str, Any], key: str, values: Iterable[str] | None) -> None:
        current = item.setdefault(key, [])
        if not isinstance(current, list):
            current = []
            item[key] = current
        for value in values or []:
            clean = model.clean_text(value, 64)
            if clean and clean not in current:
                current.append(clean)
        del current[20:]

    # ---------- 召回 ----------

    def recall(
        self,
        query: str,
        *,
        limit: int = 6,
        max_chars: int = 420,
        viewer: str = "owner",
        min_score: float = 0.2,
        cooldown_seconds: float = 600,
        reference: float | None = None,
    ) -> list[dict[str, Any]]:
        base = reference if reference is not None else model.now()
        words = model.keywords(query)
        if not words:
            return []
        recent = self.data.setdefault("meta", {}).setdefault("recent_recall", {})
        if not isinstance(recent, dict):
            recent = {}
            self.data["meta"]["recent_recall"] = recent
        for key in [key for key, value in recent.items() if base - model.as_float(value) > 21_600]:
            recent.pop(key, None)

        scored: list[tuple[float, dict[str, Any], float]] = []
        for item in self.data["items"]:
            if item.get("status", "active") != "active":
                continue
            if not _visible_to(item, viewer):
                continue
            if base - model.as_float(recent.get(item.get("id")), 0) < cooldown_seconds:
                continue
            score, _key, _eff = _score_entry(item, words, base, query)
            if score < min_score:
                continue
            scored.append((score, item, effective_weight(item, base)))
        scored.sort(key=lambda row: row[0], reverse=True)

        result: list[dict[str, Any]] = []
        used = 0
        for score, item, effective in scored:
            if len(result) >= max(1, int(limit)):
                break
            text = str(item.get("text", ""))
            if used + len(text) > max(80, int(max_chars)) and result:
                continue
            used += len(text)
            recent[str(item.get("id"))] = base
            result.append(
                {
                    "id": item.get("id"),
                    "text": text,
                    "kind": item.get("kind"),
                    "confidence": model.as_float(item.get("confidence"), 0.5),
                    "weight": round(effective, 3),
                    "score": round(score, 3),
                    "visibility": item.get("visibility", "private"),
                    "last_access": model.as_float(item.get("last_access")),
                    "created_at": model.as_float(item.get("created_at")),
                    "tags": list(item.get("tags") or []),
                    "level": item.get("level", ""),
                }
            )
        return result

    def reinforce(self, ids: Iterable[str], *, amount: float = 0.12, reference: float | None = None) -> int:
        """真正被用过才加固：权重上升，半衰期变长，访问次数增加。"""
        base = reference if reference is not None else model.now()
        touched = 0
        wanted = {str(item) for item in ids if str(item).strip()}
        for item in self.data["items"]:
            if item.get("id") not in wanted or item.get("status", "active") != "active":
                continue
            item["weight"] = min(1.6, model.as_float(item.get("weight"), 1.0) + amount)
            item["half_life_days"] = min(365.0, max(1.0, model.as_float(item.get("half_life_days"), 30.0) * 1.12))
            item["access_count"] = model.as_int(item.get("access_count")) + 1
            item["last_access"] = base
            touched += 1
        return touched

    def decay(self, reference: float | None = None, archive_below: float = 0.12) -> int:
        """按遗忘曲线变淡；足够旧、足够淡的条目归档而不是删除。"""
        base = reference if reference is not None else model.now()
        archived = 0
        for item in self.data["items"]:
            if item.get("status", "active") != "active":
                continue
            if item.get("locked"):
                continue
            if model.age_days(item.get("created_at"), base) < 14:
                continue
            if effective_weight(item, base) >= archive_below:
                continue
            item["status"] = "archived"
            item["archived_at"] = base
            item["archive_reason"] = "decayed"
            archived += 1
        return archived

    # ---------- 更正与归档 ----------

    def supersede(
        self,
        item_id: str,
        new_text: str,
        *,
        reason: str = "",
        source: str = "correction",
        confidence: float = 0.8,
    ) -> dict[str, Any]:
        old = self.find(item_id)
        if old is None or old.get("status") != "active":
            return {}
        # 先把旧条目置为非活动，避免新文本和它太像时被 add_item 当成重复项合并。
        old["status"] = "superseded"
        old["superseded_at"] = model.now()
        old["supersede_reason"] = model.clean_text(reason, 120)
        replacement = self.add_item(
            new_text,
            kind=str(old.get("kind") or "fact"),
            confidence=confidence,
            tags=old.get("tags") or [],
            evidence=[item_id],
            visibility=str(old.get("visibility") or "private"),
            source=source,
        )
        if not replacement:
            old["status"] = "active"
            old["superseded_at"] = 0.0
            old["supersede_reason"] = ""
            return {}
        old["superseded_by"] = replacement["id"]
        return replacement

    def archive(self, item_id: str, reason: str = "manual") -> bool:
        item = self.find(item_id)
        if item is None or item.get("status") != "active":
            return False
        item["status"] = "archived"
        item["archived_at"] = model.now()
        item["archive_reason"] = model.clean_text(reason, 80)
        return True

    def restore(self, item_id: str) -> bool:
        item = self.find(item_id)
        if item is None or item.get("status") != "archived":
            return False
        item["status"] = "active"
        item["archived_at"] = 0.0
        item["last_access"] = model.now()
        return True

    # ---------- 巩固 ----------

    def merge_candidates(self, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """离线巩固的候选合并：只加固或新增候选，不碰身份和权限。"""
        changed: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            text = model.clean_text(candidate.get("text"), 200)
            if len(text) < 4:
                continue
            kind = str(candidate.get("kind") or "impression")
            if kind not in ("impression", "fact", "preference", "curiosity"):
                kind = "impression"
            confidence = model.clamp(candidate.get("confidence", 0.55), 0.3, 0.9)
            item = self.add_item(
                text,
                kind=kind,
                confidence=confidence,
                tags=candidate.get("tags") or [],
                evidence=candidate.get("evidence") or [],
                visibility=str(candidate.get("visibility") or "private"),
                source=str(candidate.get("source") or "consolidation"),
                half_life_days=model.as_float(candidate.get("half_life_days"), 30.0),
            )
            if item:
                changed.append(item)
        return changed

    # ---------- 技能 ----------

    def set_skill_level(
        self,
        item_id: str,
        level: str,
        *,
        evidence: Iterable[str] | None = None,
    ) -> tuple[bool, str]:
        item = self.find(item_id)
        if item is None or item.get("kind") != "skill":
            return False, "没有找到这项技能。"
        target = str(level or "").strip().lower()
        if target not in LEVELS:
            return False, "未知的技能等级。"
        current = str(item.get("level") or "aware")
        if LEVEL_ORDER[target] <= LEVEL_ORDER.get(current, 0):
            item["level"] = target
            item["updated_at"] = model.now()
            return True, "已更新技能等级。"
        known = self.episode_ids()
        valid = [str(eid) for eid in (evidence or []) if str(eid) in known]
        if target == "learned" and not (item.get("evidence") or valid):
            return False, "至少需要一条学习来源，才能从“知道”升到“能解释”。"
        if target == "practiced" and not valid:
            return False, "需要一次真实做过的记录，才能升级到“练过”。"
        if target == "verified" and len(set(list(item.get("evidence") or []) + valid)) < 2:
            return False, "需要至少两次成功实践，才能升级到“稳定掌握”。"
        item["level"] = target
        item["updated_at"] = model.now()
        self._merge_list(item, "evidence", valid)
        return True, "已升级技能等级。"

    # ---------- 导出与统计 ----------

    def export_items(self, reference: float | None = None) -> list[dict[str, Any]]:
        base = reference if reference is not None else model.now()
        rows: list[dict[str, Any]] = []
        for item in self.data["items"]:
            if item.get("status", "active") != "active":
                continue
            rows.append(
                {
                    "id": item.get("id"),
                    "text": item.get("text"),
                    "kind": item.get("kind"),
                    "confidence": round(model.as_float(item.get("confidence"), 0.5), 3),
                    "weight": round(effective_weight(item, base), 3),
                    "tags": list(item.get("tags") or []),
                    "visibility": item.get("visibility", "private"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "last_access": item.get("last_access"),
                    "half_life_days": model.as_float(item.get("half_life_days"), 30.0),
                    "access_count": item.get("access_count", 0),
                    "level": item.get("level", ""),
                }
            )
        return rows

    def stats(self, reference: float | None = None) -> dict[str, Any]:
        base = reference if reference is not None else model.now()
        active = [item for item in self.data["items"] if item.get("status", "active") == "active"]
        by_kind: dict[str, int] = {}
        for item in active:
            kind = str(item.get("kind") or "fact")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "episodes": len(self.data["episodes"]),
            "active": len(active),
            "archived": len([i for i in self.data["items"] if i.get("status") == "archived"]),
            "superseded": len([i for i in self.data["items"] if i.get("status") == "superseded"]),
            "by_kind": by_kind,
            "strongest": round(
                max([effective_weight(item, base) for item in active] or [0.0]), 2
            ),
        }

    def pending_episodes(self, since: float = 0.0, limit: int = 40) -> list[dict[str, Any]]:
        rows = [
            item
            for item in self.data["episodes"]
            if model.as_float(item.get("at")) >= since
        ]
        rows.sort(key=lambda item: model.as_float(item.get("at")))
        return rows[-max(1, int(limit)):]

    def max_episode_at(self) -> float:
        return max([model.as_float(item.get("at")) for item in self.data["episodes"]] or [0.0])


def _visible_to(item: dict[str, Any], viewer: str) -> bool:
    visibility = str(item.get("visibility") or "private")
    if visibility in ("public", "owner"):
        return True
    if visibility == "private":
        return viewer in ("owner", "private")
    return visibility == viewer
