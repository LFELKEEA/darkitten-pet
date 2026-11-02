from __future__ import annotations

"""长期记忆 v2：证据、情节、语义、程序和元记忆都在这里落地。

几条硬规则：
- 原文只追加；驱逐前先归档，不直接丢；
- 来源有等级：更正 > 明说 > 行为验证 > 外部资料 > 推断；
- 巩固只产出候选，候选默认不参与召回，必须通过审核或明确采纳；
- 更正/撤回保留旧版和墓碑，派生条目的置信度会跟着下降；
- 召回只看可见域：私聊、群聊、本人、所有者各自隔离；
- 技能升级必须有真实实践证据，失败证据也保留。
"""

import json
import math
from pathlib import Path
from typing import Any, Iterable

from . import model

KINDS = ("impression", "fact", "preference", "skill", "curiosity")
LEVELS = ("aware", "learned", "practiced", "verified")
LEVEL_ORDER = {name: index for index, name in enumerate(LEVELS)}

SOURCE_LEVELS = ("correction", "statement", "behavior", "authority", "inference")
SOURCE_LABELS = {
    "correction": "对方更正过",
    "statement": "对方明说",
    "behavior": "实际做过",
    "authority": "公开资料",
    "inference": "推断",
}
SOURCE_PRIORITY = {name: index for index, name in enumerate(reversed(SOURCE_LEVELS))}

STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"
STATUS_SUPERSEDED = "superseded"
STATUS_DELETED = "deleted"

MAX_EPISODES = 4000
ARCHIVE_KEEP_DAYS = 3
MAX_CANDIDATES = 300


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
    if entry.get("derived_status") == "needs_review":
        confidence *= 0.6
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
        self.backup_dir = self.root / "backup"
        self.audit_path = self.root / "memory-audit.jsonl"
        self.data: dict[str, Any] = {
            "version": 2,
            "episodes": [],
            "items": [],
            "candidates": [],
            "tombstones": [],
            "meta": {},
        }
        self.load()

    # ---------- 读写 ----------

    def load(self) -> None:
        raw = model.read_json(self.path, {})
        if not isinstance(raw, dict):
            raw = {}

        def rows(key: str) -> list[dict[str, Any]]:
            value = raw.get(key)
            return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

        meta = raw.get("meta")
        self.data = {
            "version": 2,
            "episodes": rows("episodes"),
            "items": rows("items"),
            "candidates": rows("candidates"),
            "tombstones": rows("tombstones"),
            "meta": meta if isinstance(meta, dict) else {},
        }
        self._normalize()

    def _normalize(self) -> None:
        """旧版本数据迁移：补齐来源等级、可见域和主体字段。"""
        for item in self.data["items"]:
            item.setdefault("actor_id", "owner")
            item.setdefault("subject_id", "self")
            item.setdefault("visibility", "private")
            item.setdefault("source_level", _default_level(item.get("source")))
            item.setdefault("why", "")
            item.setdefault("path", "")
            item.setdefault("caveat", "")
            item.setdefault("derived_status", "")
            item.setdefault("evidence", [])
            if item.get("kind") == "skill" and not item.get("level"):
                item["level"] = "aware"
        for episode in self.data["episodes"]:
            episode.setdefault("message_id", "")
            episode.setdefault("source_level", "statement")

    def save(self) -> None:
        self.data["version"] = 2
        model.atomic_write_json(self.path, self.data)

    # ---------- 审计 ----------

    def audit(self, event: str, detail: dict[str, Any] | None = None) -> None:
        line = {
            "at": model.now(),
            "event": str(event),
            "detail": detail or {},
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.audit_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def audit_tail(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            lines = self.audit_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines[-max(1, int(limit)):]:
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict):
                rows.append(data)
        return rows

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
        message_id: str = "",
        source_level: str = "statement",
    ) -> dict[str, Any]:
        body = model.clean_text(text, 600)
        if not body:
            return {}
        stamp = model.as_float(at) or model.now()
        clean_message = model.clean_text(message_id, 80)
        if clean_message:
            existing = self._episode_by_message(clean_message)
            if existing is not None:
                return existing
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
            "source_level": source_level if source_level in SOURCE_LEVELS else "statement",
            "tags": [model.clean_text(tag, 16) for tag in (tags or []) if model.clean_text(tag, 16)][:8],
            "salience": model.clamp(salience, 0.0, 1.0),
            "fingerprint": fingerprint,
            "message_id": clean_message,
        }
        self.data["episodes"].append(item)
        if clean_message:
            seen = self.data.setdefault("meta", {}).setdefault("seen_messages", {})
            if isinstance(seen, dict):
                seen[clean_message] = stamp
                if len(seen) > 4000:
                    keep = sorted(seen.items(), key=lambda row: model.as_float(row[1]), reverse=True)[:3000]
                    self.data["meta"]["seen_messages"] = dict(keep)
        self._trim_episodes()
        return item

    def _episode_by_message(self, message_id: str) -> dict[str, Any] | None:
        for episode in reversed(self.data["episodes"]):
            if str(episode.get("message_id") or "") == message_id:
                return episode
        return None

    def has_message(self, message_id: str) -> bool:
        clean = model.clean_text(message_id, 80)
        if not clean:
            return False
        if self._episode_by_message(clean) is not None:
            return True
        seen = self.data.get("meta", {}).get("seen_messages")
        return isinstance(seen, dict) and clean in seen

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
            if status and item.get("status", STATUS_ACTIVE) != status:
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
        actor_id: str = "owner",
        subject_id: str = "self",
        source_level: str = "",
        why: str = "",
        path: str = "",
        caveat: str = "",
        message_id: str = "",
    ) -> dict[str, Any]:
        body = model.clean_text(text, 240)
        if not body:
            return {}
        valid_kind = kind if kind in KINDS else "fact"
        stamp = model.as_float(at) or model.now()
        clean_visibility = str(visibility or "private")[:48]
        level_value = source_level if source_level in SOURCE_LEVELS else _default_level(source)
        merged = self._find_similar(body, valid_kind, clean_visibility)
        if merged is not None:
            merged["weight"] = min(1.6, model.as_float(merged.get("weight"), 1.0) + 0.1)
            merged["confidence"] = min(0.95, max(model.as_float(merged.get("confidence"), 0.5), model.clamp(confidence)))
            merged["last_access"] = stamp
            merged["access_count"] = model.as_int(merged.get("access_count")) + 1
            merged["updated_at"] = stamp
            self._merge_list(merged, "tags", tags)
            self._merge_list(merged, "evidence", evidence)
            if SOURCE_PRIORITY.get(level_value, 0) > SOURCE_PRIORITY.get(str(merged.get("source_level") or ""), 0):
                merged["source_level"] = level_value
            for key, value in (("why", why), ("path", path), ("caveat", caveat)):
                if value and not merged.get(key):
                    merged[key] = model.clean_text(value, 160)
            if actor_id and not merged.get("actor_id"):
                merged["actor_id"] = str(actor_id)[:48]
            if subject_id and not merged.get("subject_id"):
                merged["subject_id"] = str(subject_id)[:48]
            return merged
        item = {
            "id": model.new_id("m"),
            "text": body,
            "kind": valid_kind,
            "status": STATUS_ACTIVE,
            "confidence": model.clamp(confidence, 0.05, 0.95),
            "weight": model.clamp(weight, 0.05, 1.6),
            "half_life_days": max(1.0, min(365.0, model.as_float(half_life_days, 30.0))),
            "created_at": stamp,
            "updated_at": stamp,
            "last_access": stamp,
            "access_count": 0,
            "tags": [model.clean_text(tag, 16) for tag in (tags or []) if model.clean_text(tag, 16)][:10],
            "evidence": [str(eid)[:64] for eid in (evidence or []) if str(eid).strip()][:20],
            "visibility": clean_visibility,
            "source": str(source or "manual")[:40],
            "source_level": level_value,
            "actor_id": str(actor_id or "owner")[:48],
            "subject_id": str(subject_id or "self")[:48],
            "why": model.clean_text(why, 160),
            "path": model.clean_text(path, 160),
            "caveat": model.clean_text(caveat, 160),
            "message_id": model.clean_text(message_id, 80),
            "superseded_by": "",
            "superseded_at": 0.0,
            "derived_status": "",
            "level": level if level in LEVELS else "",
            "locked": False,
        }
        if item["kind"] == "skill" and not item["level"]:
            item["level"] = "aware"
        self.data["items"].append(item)
        return item

    def _find_similar(self, text: str, kind: str, visibility: str = "") -> dict[str, Any] | None:
        words = model.keywords(text)
        if not words:
            return None
        for item in self.data["items"]:
            if item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
                continue
            if item.get("kind") != kind:
                continue
            if visibility and str(item.get("visibility") or "private") != visibility:
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

    # ---------- 候选 ----------

    def add_candidate(
        self,
        text: str,
        *,
        kind: str = "impression",
        confidence: float = 0.5,
        tags: Iterable[str] | None = None,
        evidence: Iterable[str] | None = None,
        visibility: str = "private",
        source: str = "consolidation",
        why: str = "",
        actor_id: str = "owner",
        subject_id: str = "self",
        source_level: str = "inference",
    ) -> dict[str, Any]:
        """巩固只写候选；候选不参与召回，等审核或明确采纳。"""
        body = model.clean_text(text, 200)
        if len(body) < 4:
            return {}
        clean_visibility = str(visibility or "private")[:48]
        digest = model.hash_text(f"{body}|{kind}|{clean_visibility}")
        for candidate in self.data["candidates"]:
            if candidate.get("digest") == digest and candidate.get("status") == "pending":
                self._merge_list(candidate, "evidence", evidence)
                return candidate
        if self._find_similar(body, kind if kind in KINDS else "impression", clean_visibility) is not None:
            return {}
        candidate = {
            "id": model.new_id("c"),
            "text": body,
            "kind": kind if kind in KINDS else "impression",
            "confidence": model.clamp(confidence, 0.2, 0.9),
            "tags": [model.clean_text(tag, 16) for tag in (tags or []) if model.clean_text(tag, 16)][:6],
            "evidence": [str(eid)[:64] for eid in (evidence or []) if str(eid).strip()][:20],
            "visibility": clean_visibility,
            "source": str(source or "consolidation")[:40],
            "source_level": source_level if source_level in SOURCE_LEVELS else "inference",
            "why": model.clean_text(why, 160),
            "actor_id": str(actor_id or "owner")[:48],
            "subject_id": str(subject_id or "self")[:48],
            "status": "pending",
            "digest": digest,
            "created_at": model.now(),
            "reviewed_at": 0.0,
            "review_reason": "",
            "item_id": "",
        }
        self.data["candidates"].append(candidate)
        del self.data["candidates"][:-MAX_CANDIDATES]
        self.audit("candidate.add", {"id": candidate["id"], "kind": candidate["kind"], "source": candidate["source"]})
        return candidate

    def candidates(self, status: str = "pending") -> list[dict[str, Any]]:
        rows = [
            row
            for row in self.data["candidates"]
            if not status or row.get("status") == status
        ]
        rows.sort(key=lambda row: model.as_float(row.get("created_at")), reverse=True)
        return rows

    def find_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for row in self.data["candidates"]:
            if row.get("id") == candidate_id:
                return row
        return None

    def review_candidate(
        self,
        candidate_id: str,
        *,
        approve: bool,
        reason: str = "",
    ) -> tuple[dict[str, Any], str]:
        candidate = self.find_candidate(candidate_id)
        if candidate is None or candidate.get("status") != "pending":
            return {}, "没有找到待审核的候选。"
        if not approve:
            candidate["status"] = "rejected"
            candidate["reviewed_at"] = model.now()
            candidate["review_reason"] = model.clean_text(reason, 120)
            self.audit("candidate.reject", {"id": candidate_id, "reason": reason})
            return candidate, "已驳回这条候选。"
        item = self.add_item(
            str(candidate.get("text") or ""),
            kind=str(candidate.get("kind") or "impression"),
            confidence=model.as_float(candidate.get("confidence"), 0.5),
            tags=candidate.get("tags") or [],
            evidence=candidate.get("evidence") or [],
            visibility=str(candidate.get("visibility") or "private"),
            source=str(candidate.get("source") or "consolidation"),
            half_life_days=60.0 if candidate.get("kind") == "impression" else 90.0,
            actor_id=str(candidate.get("actor_id") or "owner"),
            subject_id=str(candidate.get("subject_id") or "self"),
            source_level=str(candidate.get("source_level") or "inference"),
            why=str(candidate.get("why") or ""),
            caveat="由巩固候选采纳，必要时请对方确认。",
        )
        if not item:
            candidate["status"] = "rejected"
            candidate["reviewed_at"] = model.now()
            candidate["review_reason"] = "内容为空或与现有记忆重复"
            return candidate, "这条候选没有可保存的内容。"
        candidate["status"] = "approved"
        candidate["reviewed_at"] = model.now()
        candidate["review_reason"] = model.clean_text(reason, 120)
        candidate["item_id"] = item.get("id", "")
        self.audit("candidate.approve", {"id": candidate_id, "item": item.get("id")})
        return item, "已采纳为活动记忆。"

    def queue_candidates(self, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        queued: list[dict[str, Any]] = []
        for row in candidates:
            if not isinstance(row, dict):
                continue
            candidate = self.add_candidate(
                str(row.get("text") or ""),
                kind=str(row.get("kind") or "impression"),
                confidence=model.as_float(row.get("confidence"), 0.5),
                tags=row.get("tags") or [],
                evidence=row.get("evidence") or [],
                visibility=str(row.get("visibility") or "private"),
                source=str(row.get("source") or "consolidation"),
                why=str(row.get("why") or ""),
                actor_id=str(row.get("actor_id") or "owner"),
                subject_id=str(row.get("subject_id") or "self"),
                source_level=str(row.get("source_level") or "inference"),
            )
            if candidate:
                queued.append(candidate)
        return queued

    def merge_candidates(self, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        """兼容旧调用：现在只入候选队列，不再直接写活动记忆。"""
        return self.queue_candidates(candidates)

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
        allow_private: bool = True,
        channel: str = "private",
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
            if item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
                continue
            if not _visible_to(item, viewer, allow_private=allow_private, channel=channel):
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
            confidence = model.as_float(item.get("confidence"), 0.5)
            result.append(
                {
                    "id": item.get("id"),
                    "text": text,
                    "kind": item.get("kind"),
                    "confidence": confidence,
                    "confidence_label": model.confidence_label(confidence),
                    "weight": round(effective, 3),
                    "score": round(score, 3),
                    "visibility": item.get("visibility", "private"),
                    "source_level": item.get("source_level", "statement"),
                    "source_label": SOURCE_LABELS.get(str(item.get("source_level") or "statement"), "推断"),
                    "why": item.get("why", ""),
                    "path": item.get("path", ""),
                    "caveat": item.get("caveat", ""),
                    "derived_status": item.get("derived_status", ""),
                    "last_access": model.as_float(item.get("last_access")),
                    "created_at": model.as_float(item.get("created_at")),
                    "age_days": round(model.age_days(item.get("created_at"), base), 1),
                    "tags": list(item.get("tags") or []),
                    "level": item.get("level", ""),
                    "actor_id": item.get("actor_id", "owner"),
                    "subject_id": item.get("subject_id", "self"),
                }
            )
        return result

    def reinforce(self, ids: Iterable[str], *, amount: float = 0.12, reference: float | None = None) -> int:
        """真正被用过才加固：权重上升，半衰期变长，访问次数增加。"""
        base = reference if reference is not None else model.now()
        touched = 0
        wanted = {str(item) for item in ids if str(item).strip()}
        for item in self.data["items"]:
            if item.get("id") not in wanted or item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
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
            if item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
                continue
            if item.get("locked"):
                continue
            if model.age_days(item.get("created_at"), base) < 14:
                continue
            if effective_weight(item, base) >= archive_below:
                continue
            item["status"] = STATUS_ARCHIVED
            item["archived_at"] = base
            item["archive_reason"] = "decayed"
            archived += 1
        return archived

    # ---------- 更正、撤回与墓碑 ----------

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
        if old is None or old.get("status") != STATUS_ACTIVE:
            return {}
        # 先把旧条目置为非活动，避免新文本和它太像时被 add_item 当成重复项合并。
        old["status"] = STATUS_SUPERSEDED
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
            actor_id=str(old.get("actor_id") or "owner"),
            subject_id=str(old.get("subject_id") or "self"),
            source_level="correction",
            why=model.clean_text(reason, 160),
            caveat="这条是按更正后的说法保存的。",
        )
        if not replacement:
            old["status"] = STATUS_ACTIVE
            old["superseded_at"] = 0.0
            old["supersede_reason"] = ""
            return {}
        old["superseded_by"] = replacement["id"]
        self._revoke_derived(
            item_id,
            caveat="上游记忆已经更正，这条需要重新确认。",
            exclude={str(replacement.get("id") or "")},
        )
        self.audit("memory.supersede", {"old": item_id, "new": replacement.get("id"), "reason": reason})
        return replacement

    def forget(
        self,
        item_id: str,
        *,
        reason: str = "manual",
        purge: bool = True,
    ) -> bool:
        item = self.find(item_id)
        if item is None or item.get("status") == STATUS_DELETED:
            return False
        digest = model.hash_text(str(item.get("text") or ""))
        item["status"] = STATUS_DELETED
        item["deleted_at"] = model.now()
        item["delete_reason"] = model.clean_text(reason, 120)
        item["text_hash"] = digest
        if purge:
            item["text"] = "[已删除]"
            item["tags"] = []
        self.data["tombstones"].append(
            {
                "id": item_id,
                "text_hash": digest,
                "at": model.now(),
                "reason": model.clean_text(reason, 120),
            }
        )
        self._revoke_derived(item_id, caveat="上游记忆已经删除，这条需要重新确认。")
        self.audit("memory.delete", {"id": item_id, "reason": reason, "purged": bool(purge)})
        return True

    def forget_keyword(self, keyword: str, *, reason: str = "forget", purge: bool = True) -> int:
        key = model.clean_text(keyword, 60)
        if not key:
            return 0
        targets = [
            str(item.get("id"))
            for item in self.data["items"]
            if item.get("status", STATUS_ACTIVE) == STATUS_ACTIVE and key in str(item.get("text") or "")
        ]
        removed = 0
        for item_id in targets:
            if self.forget(item_id, reason=reason, purge=purge):
                removed += 1
        return removed

    def _revoke_derived(
        self,
        source_id: str,
        *,
        caveat: str,
        exclude: set[str] | None = None,
    ) -> int:
        skip = exclude or set()
        touched = 0
        for item in self.data["items"]:
            item_id = str(item.get("id") or "")
            if item_id in skip or item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
                continue
            if source_id not in [str(eid) for eid in item.get("evidence") or []]:
                continue
            item["derived_status"] = "needs_review"
            item["confidence"] = model.clamp(model.as_float(item.get("confidence"), 0.5) * 0.7, 0.05, 0.95)
            item["caveat"] = caveat
            item["updated_at"] = model.now()
            touched += 1
        if touched:
            self.audit("memory.revoke_derived", {"source": source_id, "touched": touched})
        return touched

    def archive(self, item_id: str, reason: str = "manual") -> bool:
        item = self.find(item_id)
        if item is None or item.get("status") != STATUS_ACTIVE:
            return False
        item["status"] = STATUS_ARCHIVED
        item["archived_at"] = model.now()
        item["archive_reason"] = model.clean_text(reason, 80)
        return True

    def restore(self, item_id: str) -> bool:
        item = self.find(item_id)
        if item is None or item.get("status") != STATUS_ARCHIVED:
            return False
        item["status"] = STATUS_ACTIVE
        item["archived_at"] = 0.0
        item["last_access"] = model.now()
        return True

    # ---------- 技能 ----------

    def set_skill_level(
        self,
        item_id: str,
        level: str,
        *,
        evidence: Iterable[str] | None = None,
        failed: bool = False,
    ) -> tuple[bool, str]:
        item = self.find(item_id)
        if item is None or item.get("kind") != "skill":
            return False, "没有找到这项技能。"
        target = str(level or "").strip().lower()
        if target not in LEVELS:
            return False, "未知的技能等级。"
        if failed:
            self._merge_list(item, "failed_evidence", [str(eid) for eid in (evidence or [])])
            item["last_failure_at"] = model.now()
            item["updated_at"] = model.now()
            self.audit("skill.failure", {"id": item_id, "evidence": list(evidence or [])})
            return True, "这次失败已经记下，等级不会因为失败被抹掉。"
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
        if target == "verified":
            successes = set(list(item.get("evidence") or []) + valid)
            if len(successes) < 2:
                return False, "需要至少两次成功实践，才能升级到“稳定掌握”。"
        item["level"] = target
        item["updated_at"] = model.now()
        self._merge_list(item, "evidence", valid)
        self.audit("skill.upgrade", {"id": item_id, "level": target, "evidence": valid})
        return True, "已升级技能等级。"

    # ---------- 备份与体检 ----------

    def backup(self, label: str = "auto") -> str:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = model.time.strftime("%Y%m%d-%H%M%S", model.time.localtime())
        name = f"memory-{stamp}-{model.clean_text(label, 16) or 'auto'}.json"
        target = self.backup_dir / name
        index = 1
        while target.exists():
            target = self.backup_dir / f"memory-{stamp}-{label}-{index}.json"
            index += 1
        model.atomic_write_json(target, self.data)
        files = sorted(self.backup_dir.glob("memory-*.json"), reverse=True)
        for old in files[10:]:
            try:
                old.unlink()
            except OSError:
                pass
        self.audit("memory.backup", {"name": target.name})
        return target.name

    def backups(self) -> list[dict[str, Any]]:
        try:
            files = sorted(self.backup_dir.glob("memory-*.json"), reverse=True)
        except OSError:
            return []
        return [
            {"name": path.name, "size": path.stat().st_size, "mtime": path.stat().st_mtime}
            for path in files
        ]

    def restore_backup(self, name: str) -> tuple[bool, str]:
        target = (self.backup_dir / str(name or "")).resolve()
        if self.backup_dir.resolve() not in target.parents or not target.is_file():
            return False, "找不到这个备份。"
        raw = model.read_json(target, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("items"), list):
            return False, "备份文件格式不对。"
        self.backup("before-restore")
        self.data = {
            "version": 2,
            "episodes": [row for row in raw.get("episodes") or [] if isinstance(row, dict)],
            "items": [row for row in raw.get("items") or [] if isinstance(row, dict)],
            "candidates": [row for row in raw.get("candidates") or [] if isinstance(row, dict)],
            "tombstones": [row for row in raw.get("tombstones") or [] if isinstance(row, dict)],
            "meta": raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
        }
        self._normalize()
        self.save()
        self.audit("memory.restore", {"name": target.name})
        return True, f"已从 {target.name} 恢复。"

    def integrity(self) -> dict[str, Any]:
        ids: set[str] = set()
        duplicates: list[str] = []
        missing_subject: list[str] = []
        invalid_visibility: list[str] = []
        for item in self.data["items"]:
            item_id = str(item.get("id") or "")
            if item_id in ids:
                duplicates.append(item_id)
            ids.add(item_id)
            if not item.get("subject_id"):
                missing_subject.append(item_id)
            visibility = str(item.get("visibility") or "")
            if not (visibility in ("private", "public", "owner") or visibility.startswith(("private:", "group:"))):
                invalid_visibility.append(item_id)
        orphan_evidence: list[str] = []
        known = ids | self.episode_ids()
        for item in self.data["items"]:
            for eid in item.get("evidence") or []:
                if str(eid) not in known:
                    orphan_evidence.append(str(eid))
        pending = len(self.candidates("pending"))
        return {
            "ok": not duplicates and not missing_subject and not invalid_visibility and not orphan_evidence,
            "items": len(self.data["items"]),
            "episodes": len(self.data["episodes"]),
            "duplicates": duplicates,
            "missing_subject": missing_subject,
            "invalid_visibility": invalid_visibility,
            "orphan_evidence": orphan_evidence[:20],
            "pending_candidates": pending,
            "tombstones": len(self.data["tombstones"]),
        }

    # ---------- 导出与统计 ----------

    def export_items(self, reference: float | None = None) -> list[dict[str, Any]]:
        base = reference if reference is not None else model.now()
        rows: list[dict[str, Any]] = []
        for item in self.data["items"]:
            if item.get("status", STATUS_ACTIVE) != STATUS_ACTIVE:
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
                    "actor_id": item.get("actor_id", "owner"),
                    "subject_id": item.get("subject_id", "self"),
                    "source_level": item.get("source_level", "statement"),
                    "source_label": SOURCE_LABELS.get(str(item.get("source_level") or "statement"), "推断"),
                    "why": item.get("why", ""),
                    "path": item.get("path", ""),
                    "caveat": item.get("caveat", ""),
                    "derived_status": item.get("derived_status", ""),
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
        active = [item for item in self.data["items"] if item.get("status", STATUS_ACTIVE) == STATUS_ACTIVE]
        by_kind: dict[str, int] = {}
        for item in active:
            kind = str(item.get("kind") or "fact")
            by_kind[kind] = by_kind.get(kind, 0) + 1
        return {
            "episodes": len(self.data["episodes"]),
            "active": len(active),
            "archived": len([i for i in self.data["items"] if i.get("status") == STATUS_ARCHIVED]),
            "superseded": len([i for i in self.data["items"] if i.get("status") == STATUS_SUPERSEDED]),
            "deleted": len([i for i in self.data["items"] if i.get("status") == STATUS_DELETED]),
            "by_kind": by_kind,
            "candidates": len(self.candidates("pending")),
            "tombstones": len(self.data["tombstones"]),
            "needs_review": len([i for i in active if i.get("derived_status") == "needs_review"]),
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


def _default_level(source: Any) -> str:
    value = str(source or "").lower()
    if "correct" in value:
        return "correction"
    if value in ("behavior", "practice", "verified"):
        return "behavior"
    if value in ("authority", "official", "web"):
        return "authority"
    if value in ("consolidation", "inference", "ai"):
        return "inference"
    return "statement"


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
        if visibility == channel:
            return True
        # 群聊默认不碰私聊；只有所有者本人在场且显式打开开关时才允许，
        # 而且仍然只给所有者这个视角，不是给整个群。
        if allow_private and viewer == "owner":
            return visibility in ("private", "owner") or visibility.startswith("private:")
        return False
    if visibility == "private":
        return allow_private and viewer in ("owner", "private")
    if visibility == "owner":
        return allow_private and viewer == "owner"
    if visibility.startswith("private:"):
        return allow_private and (visibility == viewer or viewer == "owner")
    return visibility == viewer
