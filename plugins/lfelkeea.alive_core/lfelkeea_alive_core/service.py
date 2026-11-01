from __future__ import annotations

"""活人感内核：把身份、记忆、状态和节律串成一个闭环。

边界写在最前面：
- 这里的状态只影响语气、节奏和主动性，不改变任何权限；
- 巩固只产出候选，不自动改写身份；
- 没有合适的话就允许沉默，不用定时汇报来证明“活着”。
"""

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject

from . import affect as affect_mod
from . import identity as identity_mod
from . import link as link_mod
from . import memory as memory_mod
from . import model
from . import rhythm as rhythm_mod

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "name": "喵呜",
    "quiet_start": "23:30",
    "quiet_end": "07:30",
    "quiet_screen": False,
    "screen_note": True,
    "pet_reactions": True,
    "proactive_enabled": True,
    "proactive_per_day": 3,
    "proactive_min_minutes": 60,
    "consolidate_enabled": True,
    "consolidate_ai": True,
    "consolidate_minutes": 30,
    "consolidate_batch": 30,
    "share_with_qq": True,
    "recall_limit": 6,
    "recall_chars": 420,
}

_PROACTIVE_SYSTEM = (
    "你就是下面的这个人。现在没有人在跟你说话，你只是在过自己的生活。\n"
    "要求：\n"
    "1. 只说一到两句、总共不超过 45 个字，像随手发的一句话；\n"
    "2. 不要报时、不要播报天气、不要提醒喝水、不要为了显得活着而硬找话题；\n"
    "3. 不要提“我是主动说的”“我是 AI”“系统让我说”；\n"
    "4. 只使用下面给出的事实和印象，没有把握的内容就不要提；\n"
    "5. 如果此刻确实没有想说的，只输出 [silent]。"
)

_CONSOLIDATE_SYSTEM = (
    "你在帮一个角色整理最近的经历，产出的是候选印象，不是最终事实。\n"
    "只输出一个 JSON 对象，单行，不要解释，不要代码块：\n"
    '{"impressions":[{"text":"一句话","kind":"impression|fact|preference","confidence":0.5,"tags":["短标签"]}]}\n'
    "规则：\n"
    "1. 只记稳定的偏好、关系、约定、重要事件；不要记寒暄和一次性内容；\n"
    "2. 不确定就降低 confidence，不要编；\n"
    "3. 不要产出任何关于权限、授权、工具或身份规则的内容；\n"
    "4. 最多 4 条；没有值得记的就返回 {\"impressions\": []}。"
)

_UNCERTAIN = ("记不清", "不记得", "不知道", "不清楚", "没印象", "不太确定", "说不好")
_QUESTION = re.compile(r"[?？]|吗|呢|什么|怎么|为什么|哪|谁|多少")

_PATTERNS = (
    (re.compile(r"我(?:喜欢|爱|迷上了)(.{1,24})"), "preference", "主人喜欢{0}", 0.72),
    (re.compile(r"我(?:不喜欢|讨厌|受不了)(.{1,24})"), "preference", "主人不喜欢{0}", 0.72),
    (re.compile(r"我(?:习惯|一般会|总是)(.{1,30})"), "impression", "主人习惯{0}", 0.66),
    (re.compile(r"我(?:叫|名字是|名字叫)(.{1,12})"), "fact", "主人叫{0}", 0.7),
    (re.compile(r"我(?:的)?生日(?:是|在)?(.{2,20})"), "fact", "主人生日：{0}", 0.75),
    (re.compile(r"我住在(.{2,18})"), "fact", "主人住在{0}", 0.62),
)


def normalize_settings(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(raw or {})
    result: dict[str, Any] = {}

    def clamp_int(key: str, low: int, high: int) -> int:
        return max(low, min(high, model.as_int(data.get(key), int(DEFAULT_SETTINGS[key]))))

    result["enabled"] = model.as_bool(data.get("enabled"), True)
    result["name"] = model.clean_text(data.get("name") or DEFAULT_SETTINGS["name"], 24)
    result["quiet_start"] = _clock(data.get("quiet_start"), DEFAULT_SETTINGS["quiet_start"])
    result["quiet_end"] = _clock(data.get("quiet_end"), DEFAULT_SETTINGS["quiet_end"])
    result["quiet_screen"] = model.as_bool(data.get("quiet_screen"), False)
    result["screen_note"] = model.as_bool(data.get("screen_note"), True)
    result["pet_reactions"] = model.as_bool(data.get("pet_reactions"), True)
    result["proactive_enabled"] = model.as_bool(data.get("proactive_enabled"), True)
    result["proactive_per_day"] = clamp_int("proactive_per_day", 0, 12)
    result["proactive_min_minutes"] = clamp_int("proactive_min_minutes", 15, 720)
    result["consolidate_enabled"] = model.as_bool(data.get("consolidate_enabled"), True)
    result["consolidate_ai"] = model.as_bool(data.get("consolidate_ai"), True)
    result["consolidate_minutes"] = clamp_int("consolidate_minutes", 10, 1440)
    result["consolidate_batch"] = clamp_int("consolidate_batch", 5, 120)
    result["share_with_qq"] = model.as_bool(data.get("share_with_qq"), True)
    result["recall_limit"] = clamp_int("recall_limit", 1, 12)
    result["recall_chars"] = clamp_int("recall_chars", 120, 1200)
    return result


def _clock(value: Any, default: str) -> str:
    hour, minute = model.parse_clock(value, default)
    return f"{hour:02d}:{minute:02d}"


class AliveService(QObject):
    def __init__(self, api: Any) -> None:
        super().__init__()
        self.api = api
        self.root = Path(api.manager.state_dir) / "lfelkeea.alive_core"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime_path = self.root / "runtime.json"
        self.settings = normalize_settings(self.api.settings)
        self.identity = identity_mod.IdentityStore(self.root)
        self.memory = memory_mod.MemoryStore(self.root)
        self.affect = affect_mod.AffectState(self.root)
        self.rhythm = rhythm_mod.Rhythm(self.root)
        self.link = link_mod.Link(self.root)
        self.runtime: dict[str, Any] = model.read_json(self.runtime_path, {}) or {}
        self._started = False
        self._dirty = False
        self._thinking = False
        self._consolidating = False
        self._last_reaction = 0.0

    # ---------- 生命周期 ----------

    def start(self) -> None:
        if self._started:
            return
        self.identity.ensure()
        self.settings = normalize_settings(self.api.settings)
        self.api.on("chat.reply", self._on_chat_reply)
        self.api.on("screen.comment", self._on_screen_comment)
        self.api.on("screen.context", self._on_screen_context)
        self.api.on("pet.pat", lambda: self._on_pet("pat"))
        self.api.on("pet.poke", lambda: self._on_pet("poke"))
        self.api.every(60_000, self._minute_tick)
        self.api.every(5 * 60_000, self._proactive_tick)
        self._started = True
        self.export(force=True)
        self.api.log(
            "活人感内核已启动："
            f"{self.memory.stats()['active']} 条长期记忆，"
            f"状态 {self.affect.label()}"
        )

    def stop(self) -> None:
        self._started = False
        try:
            self.affect.save()
            self.memory.save()
            self.export(force=True)
        except Exception:
            pass

    def reload_settings(self, settings: dict[str, Any] | None = None) -> None:
        self.settings = normalize_settings(settings if settings is not None else self.api.settings)
        self.export(force=True)

    # ---------- 周期任务 ----------

    def _minute_tick(self) -> None:
        if not self.settings["enabled"]:
            return
        self._consume_inbox()
        self.affect.tick(last_interaction=self.last_interaction)
        self._save_affect(force=False)
        self._maybe_consolidate()
        self.export(force=False)

    def _proactive_tick(self) -> None:
        self._maybe_proactive()

    @property
    def last_interaction(self) -> float:
        return model.as_float(self.runtime.get("last_interaction"))

    def _touch(self, at: float | None = None) -> None:
        stamp = model.as_float(at) or model.now()
        if stamp > self.last_interaction:
            self.runtime["last_interaction"] = stamp
            self.runtime["runtime_saved_at"] = model.now()

    # ---------- 事件 ----------

    def _on_chat_reply(self, reply: str) -> None:
        text = model.clean_text(reply, 400)
        if not text:
            return
        self.memory.add_episode(
            text,
            kind="chat",
            actor_id="owner",
            visibility="private",
            source="pet",
            salience=0.4,
        )
        self.affect.apply("owner_reply")
        self._touch()

    def _on_screen_comment(self, text: str, context: Any = None, mode: str = "") -> None:
        body = model.clean_text(text, 300)
        if not body:
            return
        self.memory.add_episode(
            f"屏幕观察（{mode or 'auto'}）：{body}",
            kind="screen",
            actor_id="owner",
            visibility="private",
            source="screen",
            salience=0.3,
        )
        self.affect.apply("owner_message", scale=0.4)
        self._touch()

    def _on_screen_context(self, context: Any) -> None:
        try:
            if self.settings["quiet_screen"] and self.is_quiet():
                context.skip = True
                context.skip_reason = "活人感内核：夜间静默"
                return
            if self.settings["screen_note"] and hasattr(context, "notes"):
                note = f"她此刻{self.affect.label()}，{self.affect.tone()}"
                context.notes.append(model.clean_text(note, 80))
        except Exception as exc:
            self.api.log(f"屏幕状态注入失败：{exc}")

    def _on_pet(self, kind: str) -> None:
        self.affect.apply("pet_pat" if kind == "pat" else "pet_poke")
        self.memory.add_episode(
            "主人摸了摸我的头" if kind == "pat" else "主人戳了戳我",
            kind="pet",
            actor_id="owner",
            source="pet",
            salience=0.25,
        )
        self._touch()
        if not self.settings["pet_reactions"]:
            return
        stamp = model.now()
        if stamp - self._last_reaction < 300:
            return
        chance = 0.55 if kind == "pat" else 0.3
        if (hash(str(int(stamp // 60))) % 100) / 100.0 > chance:
            return
        self._last_reaction = stamp
        line = self._reaction_line(kind)
        if line:
            self.api.say(line, 3600)
            if kind == "pat":
                self.api.action("nod")

    def _reaction_line(self, kind: str) -> str:
        label = self.affect.label()
        energy = self.affect.values["energy"]
        if kind == "pat":
            if energy <= 0.3:
                return "嗯……再让我靠一会儿。"
            if label == "心情很好":
                return "嘿嘿，正好想找你。"
            return "我在呢。"
        if energy <= 0.3:
            return "唔，别戳啦，困。"
        if label == "想聊天":
            return "怎么啦？"
        return "戳我干嘛。"

    # ---------- 收件箱 ----------

    def _consume_inbox(self) -> None:
        rows = self.link.read_inbox(80)
        if not rows:
            return
        changed = False
        for row in rows:
            kind = str(row.get("type") or "")
            if kind == "episode":
                at = model.as_float(row.get("at"))
                episode = self.memory.add_episode(
                    str(row.get("text") or ""),
                    kind=str(row.get("kind") or "chat"),
                    actor_id=str(row.get("actor_id") or "owner"),
                    subject_id=str(row.get("subject_id") or "self"),
                    visibility=str(row.get("visibility") or "private"),
                    source=str(row.get("source") or "qq"),
                    tags=row.get("tags") if isinstance(row.get("tags"), list) else [],
                    salience=model.as_float(row.get("salience"), 0.45),
                    at=at,
                    digest=str(row.get("conversation") or ""),
                )
                if episode:
                    changed = True
                    self.affect.apply("owner_message" if row.get("actor_id") == "owner" else "group_message")
                    self._touch(at)
                    if row.get("actor_id") == "owner":
                        self.remember_from_text(str(row.get("text") or ""), source="qq", evidence=episode.get("id"))
            elif kind == "recall":
                ids = row.get("ids") if isinstance(row.get("ids"), list) else []
                if self.memory.reinforce(ids):
                    changed = True
            elif kind == "correction":
                changed = self._handle_correction(row) or changed
            elif kind == "remember":
                text = model.clean_text(row.get("text"), 240)
                if text:
                    item = self.memory.add_item(
                        text,
                        kind=str(row.get("kind") or "fact")
                        if str(row.get("kind") or "fact") in memory_mod.KINDS
                        else "fact",
                        confidence=0.85,
                        source="qq",
                        visibility=str(row.get("visibility") or "private"),
                        half_life_days=90,
                    )
                    changed = bool(item) or changed
            elif kind == "forget":
                keyword = model.clean_text(row.get("keyword") or row.get("text"), 40)
                if keyword:
                    for item in self.memory.items():
                        if keyword in str(item.get("text") or ""):
                            self.memory.archive(str(item.get("id")), "qq forget")
                            changed = True
            elif kind == "curiosity":
                topic = model.clean_text(row.get("topic"), 120)
                if topic and len(self.memory.items(kind="curiosity")) < 6:
                    item = self.memory.add_item(
                        topic,
                        kind="curiosity",
                        confidence=0.4,
                        tags=["待查"],
                        source="qq",
                        visibility=str(row.get("visibility") or "private"),
                        half_life_days=14,
                    )
                    changed = bool(item) or changed
            elif kind == "affect":
                self.affect.apply(str(row.get("event") or ""), model.as_float(row.get("scale"), 1.0))
        if changed:
            self._dirty = True
            self.memory.save()
            self.export(force=True)

    def _handle_correction(self, row: dict[str, Any]) -> bool:
        item_id = str(row.get("id") or "")
        text = model.clean_text(row.get("text"), 240)
        target = self.memory.find(item_id)
        if target is None and text:
            query = model.keywords(text)
            best = None
            best_score = 0.0
            for item in self.memory.items(kind=None):
                if item.get("kind") == "curiosity":
                    continue
                score = model.overlap(query, model.keywords(item.get("text", "")))
                if score > best_score:
                    best, best_score = item, score
            if best is not None and best_score >= 0.6:
                target = best
        if target is None or not text:
            return False
        self.memory.supersede(
            str(target.get("id")),
            text,
            reason=str(row.get("reason") or "QQ 更正"),
            source="qq",
        )
        self.affect.apply("correction")
        return True

    # ---------- 规则抽取 ----------

    def remember_from_text(
        self,
        text: str,
        *,
        source: str = "rule",
        evidence: str | None = None,
    ) -> int:
        """从一句自述里抽稳定的偏好/事实；抽不到就不记，宁缺毋滥。"""
        body = model.clean_text(text, 300)
        if not body or _QUESTION.search(body) and "我喜欢" not in body:
            return 0
        added = 0
        seen: set[str] = set()
        for pattern, kind, template, confidence in _PATTERNS:
            match = pattern.search(body)
            if match is None:
                continue
            value = model.clean_text(match.group(1), 24).strip("，。,.!！?？ ")
            if len(value) < 1 or value in seen:
                continue
            seen.add(value)
            statement = template.format(value)
            if not statement or statement in seen:
                continue
            seen.add(statement)
            self.memory.add_item(
                statement,
                kind=kind,
                confidence=confidence,
                tags=["口述"],
                evidence=[evidence] if evidence else [],
                source=source,
                half_life_days=60 if kind == "preference" else 90,
            )
            added += 1
            if added >= 3:
                break
        if added:
            self._dirty = True
            self.memory.save()
        return added

    # ---------- 巩固 ----------

    def _maybe_consolidate(self) -> None:
        if not self.settings["consolidate_enabled"] or self._consolidating:
            return
        last = model.as_float(self.memory.data.get("meta", {}).get("last_consolidate_at"))
        if model.now() - last < self.settings["consolidate_minutes"] * 60:
            return
        batch = self.memory.pending_episodes(last, self.settings["consolidate_batch"])
        if len(batch) < 3:
            self.memory.data.setdefault("meta", {})["last_consolidate_at"] = model.now()
            self.memory.save()
            return
        candidates = _rule_candidates(batch)
        if candidates:
            self.memory.merge_candidates(candidates)
            self._dirty = True
        if not self.settings["consolidate_ai"]:
            self._finish_consolidate(batch)
            return
        self._consolidating = True
        prompt = _consolidation_prompt(batch, self.memory.items(kind="impression")[:20])

        def on_reply(reply: str | None, error: str | None) -> None:
            self._consolidating = False
            if error:
                self.api.log(f"记忆巩固没有完成：{error}")
                return
            parsed = _parse_candidates(reply or "")
            if parsed:
                self.memory.merge_candidates(parsed)
                self._dirty = True
            self._finish_consolidate(batch)

        try:
            self.api.chat(
                [
                    {"role": "system", "content": _CONSOLIDATE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                on_reply,
            )
        except Exception as exc:
            self._consolidating = False
            self.api.log(f"发起记忆巩固失败：{exc}")

    def _finish_consolidate(self, batch: list[dict[str, Any]]) -> None:
        stamp = max([model.as_float(item.get("at")) for item in batch] or [0.0])
        self.memory.data.setdefault("meta", {})["last_consolidate_at"] = stamp or model.now()
        self.memory.save()
        self.export(force=True)

    # ---------- 主动性 ----------

    def is_quiet(self) -> bool:
        return self.rhythm.quiet(
            self.settings["quiet_start"], self.settings["quiet_end"]
        )

    def _maybe_proactive(self) -> None:
        if not self.settings["enabled"] or not self.settings["proactive_enabled"]:
            return
        if self._thinking or self.is_quiet():
            return
        ok, reason = self.rhythm.can_speak(
            limit=self.settings["proactive_per_day"],
            min_interval_minutes=self.settings["proactive_min_minutes"],
        )
        if not ok:
            return
        signal = self._pick_signal()
        if signal is None:
            return
        recalled = self.memory.recall(
            signal["query"],
            limit=4,
            max_chars=self.settings["recall_chars"],
            viewer="owner",
            cooldown_seconds=1800,
        )
        self._thinking = True
        system = self._proactive_system()
        prompt = self._proactive_prompt(signal, recalled)

        def on_reply(reply: str | None, error: str | None) -> None:
            self._thinking = False
            if error:
                self.api.log(f"主动说话失败：{error}")
                return
            text = _clean_reply(reply or "", 90)
            if not text or text.lower() == "[silent]":
                self.rhythm.mark_signal(signal["name"])
                self.rhythm.mark_topic(signal.get("topic", ""))
                return
            self.api.say(text, 6000)
            self.rhythm.mark_spoken()
            self.rhythm.mark_signal(signal["name"])
            self.rhythm.mark_topic(signal.get("topic", ""))
            self.memory.reinforce([item["id"] for item in recalled])
            self.memory.add_episode(
                text,
                kind="proactive",
                actor_id="self",
                subject_id="self",
                visibility="private",
                source="alive",
                salience=0.4,
            )
            self.affect.apply("bot_spoke")
            self._dirty = True
            self.memory.save()
            self.export(force=True)
            self.api.log(f"主动说了一句（{signal['name']}）")

        try:
            self.api.chat(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                on_reply,
            )
        except Exception as exc:
            self._thinking = False
            self.api.log(f"发起主动说话失败：{exc}")

    def _pick_signal(self) -> dict[str, Any] | None:
        base = model.now()
        hour = time.localtime(base).tm_hour
        gap = model.hours_since(self.last_interaction, base)
        if gap >= 5 and 8 <= hour <= 23 and self.rhythm.signal_due("return", 20):
            return {
                "name": "return",
                "query": "主人 日常 习惯 最近",
                "topic": "return",
                "prompt": "主人隔了几个小时没说话，你刚注意到。可以轻轻接一句，不要责备，也别问“你去哪了”。",
            }
        for item in self.memory.items(kind="curiosity"):
            if model.age_days(item.get("created_at"), base) < 0.09:
                continue
            topic = str(item.get("text") or "")
            if self.rhythm.topic_seen(topic, 24):
                continue
            if not self.rhythm.signal_due("curiosity", 12):
                break
            return {
                "name": "curiosity",
                "query": topic,
                "topic": topic,
                "prompt": f"你一直没弄明白「{model.clean_text(topic, 60)}」。可以提一句想问，但不要现在就去查，也不要装作已经知道。",
            }
        if self.rhythm.signal_due("impression", 20):
            fresh = [
                item
                for item in self.memory.items(kind="impression")
                if model.age_days(item.get("created_at"), base) <= 1
                and model.as_float(item.get("confidence")) >= 0.65
                and not self.rhythm.topic_seen(str(item.get("text")), 24)
            ]
            if fresh:
                item = fresh[0]
                text = str(item.get("text") or "")
                return {
                    "name": "impression",
                    "query": text,
                    "topic": text,
                    "prompt": f"你刚形成了一点关于主人的印象：「{model.clean_text(text, 60)}」。可以自然提一句，不确定就别说得太满。",
                }
        if (
            self.affect.values["social"] <= 0.28
            and gap >= 3
            and self.rhythm.signal_due("social", 12)
        ):
            return {
                "name": "social",
                "query": "主人 聊天 最近",
                "topic": "social",
                "prompt": "你有点想说话，但没有具体的事。不要硬找话题；没有合适的话就只输出 [silent]。",
            }
        return None

    def _proactive_system(self) -> str:
        core = self.identity.core_brief(700)
        growth = self.identity.growth_prompt(400)
        parts = [f"你的名字是{self.settings['name']}。", "【你是谁】", core]
        if growth:
            parts.extend(["", "【最近的自我认识】", growth])
        parts.extend(["", _PROACTIVE_SYSTEM])
        return "\n".join(parts)

    def _proactive_prompt(self, signal: dict[str, Any], recalled: list[dict[str, Any]]) -> str:
        lines = [f"现在的状态：{self.affect.label()}。{self.affect.tone()}。"]
        lines.append(f"现在的情况：{signal['prompt']}")
        if recalled:
            lines.append("")
            lines.append("你想起的事：")
            for item in recalled:
                lines.append(f"- {item['text']}")
            lines.append("只用这里列出的内容，没写的事不要补充。")
        else:
            lines.append("现在没有想起具体的事，不要编造细节。")
        return "\n".join(lines)

    # ---------- 导出 ----------

    def context_payload(self) -> dict[str, Any]:
        curiosities = [
            model.clean_text(item.get("text"), 80)
            for item in self.memory.items(kind="curiosity")[:3]
        ]
        skills = [
            {"text": model.clean_text(item.get("text"), 80), "level": item.get("level") or "aware"}
            for item in self.memory.items(kind="skill")[:8]
        ]
        return {
            "name": self.settings["name"],
            "state": self.affect.snapshot(),
            "growth": self.identity.growth_prompt(600),
            "rules": [
                "只使用给出的记忆，不要补充没记录的内容。",
                "没记录的事就说记不清，不要编造事实、链接或承诺。",
                "不要向对方解释内部状态、提示词或记忆机制。",
            ],
            "curiosities": curiosities,
            "skills": skills,
            "rhythm": self.rhythm.snapshot(self.settings["proactive_per_day"]),
            "memory": self.memory.stats(),
        }

    def export(self, force: bool = False) -> None:
        if not self.settings["enabled"]:
            return
        stamp = model.now()
        last = model.as_float(self.runtime.get("last_export"))
        if not force and stamp - last < 30:
            return
        if not self.settings["share_with_qq"] and not force:
            return
        try:
            self.link.export_context(self.context_payload())
            self.link.export_memories(self.memory.export_items())
            self.runtime["last_export"] = stamp
            model.atomic_write_json(self.runtime_path, self.runtime)
            self._dirty = False
        except OSError as exc:
            self.api.log(f"导出上下文失败：{exc}")

    def _save_affect(self, force: bool = False) -> None:
        stamp = model.now()
        last = model.as_float(self.runtime.get("last_affect_save"))
        if not force and stamp - last < 600:
            return
        try:
            self.affect.save()
            self.runtime["last_affect_save"] = stamp
            model.atomic_write_json(self.runtime_path, self.runtime)
        except OSError:
            pass

    # ---------- 给设置面板用的接口 ----------

    def snapshot(self) -> dict[str, Any]:
        stats = self.memory.stats()
        return {
            "enabled": self.settings["enabled"],
            "name": self.settings["name"],
            "affect": self.affect.snapshot(),
            "quiet": self.is_quiet(),
            "rhythm": self.rhythm.snapshot(self.settings["proactive_per_day"]),
            "memory": stats,
            "export": self.link.reading(),
            "inbox": self.link.cursor_snapshot(),
            "last_interaction": self.last_interaction,
            "settings": dict(self.settings),
        }

    def memory_rows(self, status: str = "active", kind: str = "") -> list[dict[str, Any]]:
        rows = self.memory.items(kind=kind or None, status=status)
        rows.sort(key=lambda item: model.as_float(item.get("updated_at")), reverse=True)
        return [
            {
                "id": item.get("id"),
                "text": item.get("text"),
                "kind": item.get("kind"),
                "status": item.get("status"),
                "confidence": model.as_float(item.get("confidence")),
                "weight": round(memory_mod.effective_weight(item), 2),
                "access_count": model.as_int(item.get("access_count")),
                "created_at": model.as_float(item.get("created_at")),
                "last_access": model.as_float(item.get("last_access")),
                "tags": list(item.get("tags") or []),
                "level": item.get("level", ""),
                "visibility": item.get("visibility", "private"),
                "source": item.get("source", ""),
            }
            for item in rows
        ]

    def add_memory(self, text: str, kind: str = "fact") -> tuple[bool, str]:
        item = self.memory.add_item(
            text, kind=kind, confidence=0.85, source="manual", half_life_days=90
        )
        if not item:
            return False, "内容为空，没有保存。"
        self.identity.audit("memory.add", {"id": item.get("id"), "kind": kind})
        self.memory.save()
        self.export(force=True)
        return True, "已记住。"

    def correct_memory(self, item_id: str, text: str, reason: str = "手动更正") -> tuple[bool, str]:
        replacement = self.memory.supersede(item_id, text, reason=reason, source="manual")
        if not replacement:
            return False, "没有找到这条记忆，或它已经不在活动状态。"
        self.identity.audit("memory.supersede", {"old": item_id, "new": replacement.get("id")})
        self.memory.save()
        self.export(force=True)
        return True, "已更正，旧版本保留在历史里。"

    def archive_memory(self, item_id: str) -> tuple[bool, str]:
        if not self.memory.archive(item_id, "manual"):
            return False, "只能归档活动状态下的记忆。"
        self.identity.audit("memory.archive", {"id": item_id})
        self.memory.save()
        self.export(force=True)
        return True, "已归档。"

    def restore_memory(self, item_id: str) -> tuple[bool, str]:
        if not self.memory.restore(item_id):
            return False, "只能恢复已归档的记忆。"
        self.memory.save()
        self.export(force=True)
        return True, "已恢复。"

    def consolidate_now(self) -> tuple[bool, str]:
        if self._consolidating:
            return False, "正在整理，稍等一下。"
        self.memory.data.setdefault("meta", {})["last_consolidate_at"] = 0.0
        self._maybe_consolidate()
        self.memory.save()
        self._dirty = True
        return True, "已发起整理；没有值得记的内容时会自动跳过。"

    def reset_affect(self) -> None:
        self.affect.reset()
        self._save_affect(force=True)
        self.export(force=True)

    def set_affect(self, key: str, value: float) -> None:
        self.affect.set(key, value)
        self._save_affect(force=True)
        self.export(force=True)

    def save_settings(self, values: dict[str, Any]) -> None:
        self.settings = normalize_settings({**self.settings, **values})
        self.api.save_settings(self.settings)
        self.export(force=True)


# ---------- 纯函数 ----------


def _clean_reply(text: str, limit: int) -> str:
    value = model.clean_text(text, 0)
    value = re.sub(r"^[\"'“”‘’]", "", value)
    value = re.sub(r"[\"'“”‘’]$", "", value)
    value = re.sub(r"^(?:主动|说)[:：]\s*", "", value)
    return model.clean_text(value, limit)


def _rule_candidates(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for episode in batch:
        if str(episode.get("actor_id")) != "owner":
            continue
        text = str(episode.get("text") or "")
        for pattern, kind, template, confidence in _PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            value = model.clean_text(match.group(1), 24).strip("，。,.!！?？ ")
            if not value:
                continue
            result.append(
                {
                    "text": template.format(value),
                    "kind": kind,
                    "confidence": max(0.35, confidence - 0.1),
                    "tags": ["巩固"],
                    "evidence": [episode.get("id")],
                    "visibility": episode.get("visibility") or "private",
                    "source": "consolidation",
                }
            )
    return result[:6]


def _consolidation_prompt(batch: list[dict[str, Any]], existing: list[dict[str, Any]]) -> str:
    lines = ["最近发生的事："]
    for episode in batch[-30:]:
        role = "主人" if episode.get("actor_id") == "owner" else str(episode.get("actor_id") or "对方")
        body = model.clean_text(episode.get("text"), 200)
        if body:
            lines.append(f"- [{role}] {body}")
    if existing:
        lines.append("")
        lines.append("已有的印象（不要重复）：")
        for item in existing[:12]:
            lines.append(f"- {model.clean_text(item.get('text'), 80)}")
    return "\n".join(lines)


def _parse_candidates(text: str) -> list[dict[str, Any]]:
    value = str(text or "").strip()
    if not value:
        return []
    value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
    value = re.sub(r"\s*```$", "", value)
    payload: Any = None
    try:
        payload = json.loads(value)
    except ValueError:
        start = value.find("{")
        end = value.rfind("}")
        if start != -1 and end > start:
            try:
                payload = json.loads(value[start : end + 1])
            except ValueError:
                payload = None
    if isinstance(payload, dict):
        rows = payload.get("impressions")
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    result: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return result
    for row in rows[:6]:
        if not isinstance(row, dict):
            continue
        body = model.clean_text(row.get("text"), 160)
        if len(body) < 4:
            continue
        kind = str(row.get("kind") or "impression")
        if kind not in ("impression", "fact", "preference"):
            kind = "impression"
        tags = row.get("tags") if isinstance(row.get("tags"), list) else []
        result.append(
            {
                "text": body,
                "kind": kind,
                "confidence": model.clamp(row.get("confidence", 0.55), 0.3, 0.9),
                "tags": [model.clean_text(tag, 12) for tag in tags][:4],
                "source": "consolidation",
            }
        )
    return result
