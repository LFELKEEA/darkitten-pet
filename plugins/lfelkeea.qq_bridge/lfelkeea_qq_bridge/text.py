from __future__ import annotations

"""QQ / OneBot 消息解析与回复清洗。"""

import html
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_FACE_RE = re.compile(r"\[face:(\d{1,4})\]", re.IGNORECASE)
_SILENT_RE = re.compile(r"\[(?:silent|quiet|安静|不说话)\]", re.IGNORECASE)
_VOICE_RE = re.compile(r"[\[【](?:voice|语音|发语音)[\]】]", re.IGNORECASE)
_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_.-]+)((?:,[^\]]*)?)\]")
_TEXT_LIMIT = 4000


@dataclass(slots=True)
class IncomingMessage:
    post_type: str
    message_type: str
    message_id: str
    self_id: str
    user_id: str
    group_id: str
    sender_name: str
    text: str
    raw_text: str
    segments: list[dict[str, Any]]
    at_self: bool
    has_image: bool
    face_count: int
    reply_to: str
    time: int
    conversation: str

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def is_private(self) -> bool:
        return self.message_type == "private"


def _as_id(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _normalize_segments(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        segments: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type", "")).strip()
            data = item.get("data")
            segments.append(
                {"type": kind, "data": dict(data) if isinstance(data, dict) else {}}
            )
        return segments
    if isinstance(raw, str):
        return _parse_cq_string(raw)
    return []


def _cq_unescape(value: str) -> str:
    return (
        value.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#44;", ",")
        .replace("&amp;", "&")
    )


def _parse_cq_string(raw: str) -> list[dict[str, Any]]:
    """兼容 SnowLuma 的 messageFormat=string（CQ 码）模式。"""
    segments: list[dict[str, Any]] = []
    cursor = 0
    for match in _CQ_RE.finditer(raw):
        if match.start() > cursor:
            text = _cq_unescape(raw[cursor : match.start()])
            if text:
                segments.append({"type": "text", "data": {"text": text}})
        kind = match.group(1)
        data: dict[str, str] = {}
        for part in match.group(2).split(","):
            if not part or "=" not in part:
                continue
            key, value = part.split("=", 1)
            data[key.strip()] = _cq_unescape(value.strip())
        segments.append({"type": kind, "data": data})
        cursor = match.end()
    if cursor < len(raw):
        text = _cq_unescape(raw[cursor:])
        if text:
            segments.append({"type": "text", "data": {"text": text}})
    return segments


def _sender_name(payload: dict[str, Any], user_id: str) -> str:
    sender = payload.get("sender")
    if isinstance(sender, dict):
        for key in ("card", "nickname", "name"):
            value = str(sender.get(key, "") or "").strip()
            if value:
                return value
    return user_id


def parse_event(payload: Any, *, include_sent: bool = False) -> IncomingMessage | None:
    """把 OneBot 事件解析成 IncomingMessage；不是可处理消息时返回 None。"""
    if not isinstance(payload, dict):
        return None
    post_type = str(payload.get("post_type", "") or "")
    if post_type == "message_sent":
        if not include_sent:
            return None
    elif post_type != "message":
        return None

    message_type = str(payload.get("message_type", "") or "").strip().lower()
    group_id = _as_id(payload.get("group_id"))
    user_id = _as_id(payload.get("user_id"))
    if message_type not in ("group", "private"):
        message_type = "group" if group_id else "private"
    if message_type == "group" and not group_id:
        return None
    if message_type == "private" and not user_id:
        return None

    self_id = _as_id(payload.get("self_id"))
    segments = _normalize_segments(payload.get("message"))
    text_parts: list[str] = []
    at_self = False
    has_image = False
    face_count = 0
    reply_to = ""

    for segment in segments:
        kind = str(segment.get("type", "") or "")
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        if kind == "text":
            text_parts.append(str(data.get("text", "") or ""))
        elif kind == "at":
            qq = _as_id(data.get("qq"))
            name = str(data.get("name", "") or "").strip()
            if self_id and qq == self_id:
                at_self = True
                text_parts.append("@你")
            elif name:
                text_parts.append("@" + name)
            elif qq:
                text_parts.append("@" + qq)
            else:
                text_parts.append("@")
        elif kind == "face":
            face_count += 1
            face_id = _as_id(data.get("id"))
            text_parts.append(f"[face:{face_id}]" if face_id else "[表情]")
        elif kind == "mface":
            face_count += 1
            text_parts.append("[表情]")
        elif kind == "image":
            has_image = True
            text_parts.append("[图片]")
        elif kind == "reply":
            reply_to = _as_id(data.get("id"))
            text_parts.append("[回复]")
        elif kind == "forward":
            text_parts.append("[合并转发]")
        elif kind in ("json", "xml"):
            text_parts.append("[卡片]")
        elif kind == "record":
            text_parts.append("[语音]")
        elif kind == "video":
            text_parts.append("[视频]")
        elif kind == "file":
            text_parts.append("[文件]")
        elif kind == "poke":
            text_parts.append("[戳一戳]")
        else:
            fallback = str(data.get("text", "") or "").strip()
            if fallback:
                text_parts.append(fallback)

    text = "".join(text_parts).strip()
    if len(text) > _TEXT_LIMIT:
        text = text[:_TEXT_LIMIT]

    raw_message = payload.get("message")
    raw_text = raw_message if isinstance(raw_message, str) else text
    if len(raw_text) > _TEXT_LIMIT:
        raw_text = raw_text[:_TEXT_LIMIT]

    try:
        timestamp = int(payload.get("time") or 0)
    except (TypeError, ValueError):
        timestamp = 0

    conversation = (
        f"group:{group_id}" if message_type == "group" else f"private:{user_id}"
    )
    return IncomingMessage(
        post_type=post_type,
        message_type=message_type,
        message_id=_as_id(payload.get("message_id")),
        self_id=self_id,
        user_id=user_id,
        group_id=group_id,
        sender_name=_sender_name(payload, user_id),
        text=text,
        raw_text=raw_text,
        segments=segments,
        at_self=at_self,
        has_image=has_image,
        face_count=face_count,
        reply_to=reply_to,
        time=timestamp,
        conversation=conversation,
    )


def strip_bot_prefix(text: str, bot_name: str) -> str:
    value = str(text or "").strip()
    name = str(bot_name or "").strip()
    if not value or not name:
        return value
    for prefix in ("@你", "＠你", f"@{name}", f"＠{name}", name):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return value.lstrip(" :：,，、-—").strip()


def is_addressed(
    message: IncomingMessage,
    bot_name: str,
    group_trigger: str = "at",
    reply_chance: float = 0.15,
    rng=random.random,
) -> bool:
    if message.is_private:
        return True
    if message.at_self:
        return True
    text = str(message.text or "").lstrip()
    name = str(bot_name or "").strip()
    if name and (text.startswith(name) or text.startswith("@" + name) or text.startswith("＠" + name)):
        return True
    if str(group_trigger or "").lower() == "all":
        try:
            chance = max(0.0, min(1.0, float(reply_chance)))
        except (TypeError, ValueError):
            chance = 0.15
        return rng() < chance
    return False


def image_sources(message: IncomingMessage, limit: int = 3) -> list[str]:
    """从消息段里取出可访问的图片地址（http/file/本地绝对路径）。"""
    sources: list[str] = []
    for segment in message.segments:
        if str(segment.get("type", "") or "") != "image":
            continue
        data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
        for key in ("url", "file", "path"):
            value = str(data.get(key, "") or "").strip().strip('"')
            if not value:
                continue
            if value.startswith(("http://", "https://", "file://")):
                sources.append(value)
                break
            candidate = Path(value)
            if candidate.is_absolute() and candidate.is_file():
                sources.append(str(candidate))
                break
        if len(sources) >= limit:
            break
    return sources


def clean_incoming_text(text: str, bot_name: str = "", limit: int = 1800) -> str:
    value = strip_bot_prefix(text, bot_name)
    value = _FACE_RE.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if len(value) > limit:
        value = value[:limit].rstrip() + "…"
    return value


def parse_face_tags(text: str) -> tuple[str, list[str]]:
    faces: list[str] = []
    for match in _FACE_RE.finditer(text or ""):
        face_id = match.group(1)
        if face_id not in faces:
            faces.append(face_id)
    cleaned = _FACE_RE.sub("", text or "")
    return cleaned, faces[:3]


def parse_voice_tag(text: str) -> tuple[str, bool]:
    """AI 在回复里写 [voice]/[语音] 时，去掉标记并要求附带一条语音。"""
    value = str(text or "")
    found = bool(_VOICE_RE.search(value))
    if found:
        value = _VOICE_RE.sub("", value)
    return value, found


def is_silent(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return True
    if value in ("silent", "quiet", "[silent]", "（沉默）", "(沉默)"):
        return True
    return bool(_SILENT_RE.fullmatch(value))


def sanitize_reply(text: str, max_chars: int = 300) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        return ""
    if is_silent(value):
        return ""
    # 去掉常见 Markdown 外壳，避免在 QQ 里出现一堆符号
    value = re.sub(r"```[a-zA-Z0-9_-]*\n?", "", value)
    value = value.replace("```", "")
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"__(.+?)__", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"\1", value)
    value = re.sub(r"^[ \t]*[-*•]\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"^[ \t]*#{1,6}\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"^[ \t]*(小猫|助手|assistant)\s*[:：]\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip().strip("\"'“”‘’")

    try:
        limit = max(20, min(1000, int(max_chars)))
    except (TypeError, ValueError):
        limit = 300
    if len(value) <= limit:
        return value
    cut = value[:limit]
    for mark in ("。", "！", "？", "!", "?", "\n", "；", ";"):
        position = cut.rfind(mark)
        if position >= limit * 0.55:
            return cut[: position + 1].rstrip()
    return cut.rstrip() + "…"


def build_segments(
    text: str,
    faces: list[str] | None = None,
    images: list[str] | None = None,
    records: list[str] | None = None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    value = str(text or "").strip()
    if value:
        segments.append({"type": "text", "data": {"text": value}})
    for face_id in faces or []:
        face = str(face_id or "").strip()
        if face.isdigit() and 0 <= int(face) <= 999:
            segments.append({"type": "face", "data": {"id": face}})
    for image in images or []:
        path = str(image or "").strip()
        if path:
            segments.append({"type": "image", "data": {"file": path}})
    for record in records or []:
        path = str(record or "").strip()
        if path:
            segments.append({"type": "record", "data": {"file": path}})
    return segments


def split_text(text: str, limit: int = 1500) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    try:
        size = max(200, min(3000, int(limit)))
    except (TypeError, ValueError):
        size = 1500
    chunks: list[str] = []
    current = ""
    for line in value.splitlines():
        candidate = (current + "\n" + line) if current else line
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > size:
            chunks.append(line[:size])
            line = line[size:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def unescape_html_text(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", html.unescape(str(value or ""))).strip()
