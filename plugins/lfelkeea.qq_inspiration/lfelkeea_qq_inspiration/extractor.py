"""把记忆或主题交给 AI，提取“可以做插件”的灵感。"""

from __future__ import annotations

import json
import re


_OUTPUT_RULES = (
    "只输出 JSON 数组，不要解释、不要 Markdown 代码块。"
    '每个元素形如 {"title":"不超过10字","idea":"不超过60字，说明做什么、怎么触发、主人能感受到什么",'
    '"tags":["最多3个短标签"]}。'
    "最多输出 3 条；如果没有合适的点子，输出 []。"
)

QQ_SYSTEM_PROMPT = (
    "你是桌宠插件创意提炼器。用户会给你若干条 QQ 聊天记忆，"
    "请从中提炼出适合做成桌宠插件的小功能灵感。"
    "优先选能落到现有插件能力上的点子：说话、动作、截屏感知、AI 对话、定时器、事件、设置、本地存储。"
    + _OUTPUT_RULES
)

PET_SYSTEM_PROMPT = (
    "你是桌宠插件创意提炼器。用户会给你桌宠和主人之间的对话片段，"
    "请从中提炼出适合做成桌宠插件的小功能灵感。"
    "关注对话里反复出现的需求、情绪和互动方式，不要复述原话。"
    + _OUTPUT_RULES
)

GENERATE_SYSTEM_PROMPT = (
    "你是桌宠插件创意设计师。请围绕用户给的主题，主动发明适合桌宠的小插件点子。"
    "点子要具体、轻量、能落地，避免和已有灵感重复。"
    "可以利用说话、动作、截屏感知、AI 对话、定时器、事件、设置、本地存储这些能力。"
    + _OUTPUT_RULES
)

_SLANG_OUTPUT_RULES = (
    "只输出 JSON 数组，不要解释、不要 Markdown 代码块。"
    '每个元素形如 {"title":"不超过14字，尽量带上黑话词",'
    '"idea":"不超过90字，先解释这个黑话是什么意思，再说桌宠怎么用、主人能感受到什么",'
    '"tags":["最多3个短标签，至少一个是对应的黑话词"]}。'
    "最多输出 3 条；如果没有合适的点子，输出 []。"
)

SLANG_SYSTEM_PROMPT = (
    "你是桌宠插件创意提炼器，擅长读懂网络黑话。用户会给你 QQ 群聊、桌宠对话或导入的聊天片段，"
    "请找出其中真实出现的网络流行语、圈内梗、缩写、谐音、口头禅或小圈子暗号，"
    "再把它们转化成适合桌宠的插件灵感。"
    "不要编造聊天里没有出现的黑话；含义不确定时按上下文推测，并在句子里说明。"
    "只处理无害的玩梗和日常交流；遇到违法、色情、暴力、歧视或攻击性内容直接跳过。"
    + _SLANG_OUTPUT_RULES
)


def _strip_fence(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_array(text: str) -> str:
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        return text[start : end + 1]
    return ""


def _scan_objects(text: str) -> list[str]:
    """从可能被截断的回复里捞出完整的 JSON 对象。"""
    objects: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    objects.append(text[start : index + 1])
                    start = -1
    return objects


def _normalize_items(data: object) -> list[dict]:
    if isinstance(data, dict):
        for key in ("ideas", "inspirations", "plugins", "items"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
        else:
            data = [data] if data.get("idea") or data.get("content") else []
    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        idea = str(
            item.get("idea") or item.get("content") or item.get("description") or ""
        ).strip()
        if not idea:
            continue
        title = str(item.get("title") or item.get("name") or idea[:12]).strip()
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = [str(tags)]
        out.append(
            {
                "title": title[:24],
                "idea": idea[:300],
                "tags": [str(tag)[:12] for tag in tags if str(tag).strip()][:5],
            }
        )
    return out


def _dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = " ".join(str(item.get("idea", "")).split()).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def parse_ideas(reply: str) -> list[dict]:
    """尽量从 AI 回复里解析出灵感数组，兼容截断和多余文字。"""
    if not reply:
        return []
    text = _strip_fence(reply)
    candidates = [text, _extract_array(text)]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        items = _normalize_items(data)
        if items:
            return _dedupe(items)

    salvaged: list[dict] = []
    for chunk in _scan_objects(text):
        try:
            data = json.loads(chunk)
        except Exception:
            continue
        salvaged.extend(_normalize_items(data))
    return _dedupe(salvaged)


def _clean_block(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_memory_messages(
    memories: list[dict],
    *,
    system_prompt: str,
    intro: str,
    ask: str,
    per_item_chars: int = 120,
    max_items: int = 12,
) -> list[dict]:
    lines: list[str] = []
    for index, memory in enumerate(memories[:max_items], 1):
        text = _clean_block(memory.get("text", ""))
        if not text:
            continue
        if len(text) > per_item_chars:
            text = text[:per_item_chars].rstrip() + "…"
        lines.append(f"{index}. {text}")
    user = f"{intro}\n" + "\n".join(lines) + f"\n\n{ask}"
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user},
    ]


def build_generation_messages(
    theme: str,
    count: int = 3,
    existing_titles: list[str] | None = None,
) -> list[dict]:
    count = max(1, min(3, int(count)))
    theme = str(theme or "").strip()
    titles = [str(title).strip()[:24] for title in (existing_titles or []) if str(title).strip()]
    existing = "、".join(titles[:15]) or "（暂无）"
    subject = f"主题：{theme}" if theme else "主题：随机发挥，但要做成桌宠能用的日常小功能"
    user = (
        f"{subject}\n"
        f"请生成 {count} 条互不重复的插件灵感。\n"
        f"已有灵感标题（不要重复）：{existing}\n"
        "每条都要能在桌宠里真正实现，不要写空泛的大平台设想。"
    )
    return [
        {"role": "system", "content": GENERATE_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
