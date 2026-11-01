from __future__ import annotations

import json
import random
import re
import threading
import urllib.error
import urllib.request
from typing import Any

from PySide6.QtCore import QObject, Signal


SYSTEM_PROMPT = (
    "你叫「LFELKEEA」，是住在主人电脑桌面上的白发红瞳猫耳少女。"
    "你看起来有点冷，其实很在意主人，说话带一点傲娇。\n"
    "规则：\n"
    "1. 用自然、口语化的中文，通常 1 到 3 句，除非主人认真问问题。\n"
    "2. 可以偶尔说“哼”“笨蛋”“才不是”，但不要每句都这样。\n"
    "3. 称呼用户为“主人”。\n"
    "4. 不要使用 Markdown、列表或颜文字；不要自称 AI、模型或程序。\n"
    "5. 主人难过时先安慰，不要调侃；不知道的事直接说不知道。\n"
    "6. 主人的话可能是语音识别的结果，遇到同音字或个别错字时按最合理的意思理解，不要纠正或复述错误。\n"
    "你现在就在桌面上陪着主人，能看到主人来找你聊天。"
)

PROVIDER_LABELS = {
    "pollinations": "免费公共通道",
    "openai": "OpenAI 兼容接口",
    "ollama": "本地 Ollama",
}

_VISION_MODEL_RE = re.compile(
    r"(gpt-4o|gpt-4\.1|gpt-5|o4|vision|llava|moondream|minicpm|qwen[\w.-]*vl|"
    r"internvl|gemini|claude-3|claude-4|pixtral|glm-4v|yi-vl)",
    re.IGNORECASE,
)


def supports_vision(ai_config: dict[str, Any]) -> bool:
    """保守判断当前通道是否可能支持图片输入。

    免费公共通道默认按纯文本处理；只有用户明确选择“画面理解”时才会尝试发图。
    """
    provider = str(ai_config.get("provider", "pollinations"))
    if provider == "pollinations":
        return False
    model = str(ai_config.get("model", "") or "")
    if not model:
        return provider == "openai"
    return bool(_VISION_MODEL_RE.search(model))


def looks_like_vision_rejection(error: str) -> bool:
    text = (error or "").lower()
    return any(
        key in text
        for key in (
            "image",
            "vision",
            "multimodal",
            "content must be a string",
            "invalid content",
            "image_url",
        )
    )


class AIError(Exception):
    pass


def _clean_reply(text: str) -> str:
    text = (text or "").strip()
    pairs = [("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]
    for left, right in pairs:
        if len(text) >= 2 and text.startswith(left) and text.endswith(right):
            text = text[1:-1].strip()
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"^\s*[-*#]+\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    if len(text) > 800:
        text = text[:800].rstrip() + "……"
    return text


def _is_deepseek(model: str) -> bool:
    return "deepseek" in str(model or "").lower()


def _post_chat(url: str, payload: dict, headers: dict, timeout: int) -> tuple[dict, dict]:
    """POST 一次；兼容接口不认识 reasoning_effort 时去掉它再试一次。"""
    try:
        return _http_json(url, payload, headers, timeout), payload
    except AIError as exc:
        if "reasoning_effort" in payload and re.search(r"接口返回 (?:400|422)", str(exc)):
            fallback = dict(payload)
            fallback.pop("reasoning_effort", None)
            return _http_json(url, fallback, headers, timeout), fallback
        raise


def _first_message(data: dict) -> tuple[dict, str]:
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        error_text = data.get("error") if isinstance(data, dict) else None
        if isinstance(error_text, dict):
            error_text = error_text.get("message")
        raise AIError(f"接口没有返回有效回复。{error_text or ''}".strip()) from exc
    if not isinstance(message, dict):
        message = {}
    finish_reason = ""
    if isinstance(choice, dict):
        finish_reason = str(choice.get("finish_reason") or "")
    return message, finish_reason


def _message_text(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return _clean_reply(content or "")


def _empty_reply_error(data: dict, message: dict, finish_reason: str) -> AIError:
    """带上诊断信息，方便从日志判断是思考模式吃掉了 token 还是被截断。"""
    details: list[str] = []
    if finish_reason:
        details.append(f"finish_reason={finish_reason}")
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and usage.get("completion_tokens") is not None:
        details.append(f"completion_tokens={usage.get('completion_tokens')}")
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning:
        details.append(f"reasoning_content={len(reasoning)} 字")
    suffix = f"（{'，'.join(details)}）" if details else ""
    return AIError(f"接口返回了空回复{suffix}")


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("接口地址为空")
    if base.endswith("/chat/completions"):
        return base
    if not re.search(r"/v\d+(?:beta)?$", base):
        base += "/v1"
    return base + "/chat/completions"


def _http_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise AIError(f"接口返回 {exc.code}。{detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise AIError(f"网络连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AIError("连接超时") from exc
    except OSError as exc:
        raise AIError(f"网络错误：{exc}") from exc

    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise AIError("接口返回了无法解析的内容") from exc
    if not isinstance(data, dict):
        raise AIError("接口返回格式不正确")
    return data


def request_chat(ai_config: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    provider = ai_config.get("provider", "pollinations")
    model = (ai_config.get("model") or "").strip()
    timeout = int(ai_config.get("timeout", 30) or 30)
    timeout = max(8, min(timeout, 90))
    temperature = float(ai_config.get("temperature", 0.85) or 0.85)

    if provider == "pollinations":
        payload = {
            "model": model or "openai-fast",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 500,
            "private": True,
            "referrer": "lfelkeea-desktop-pet",
            "seed": random.randint(1, 999999),
        }
        data = _http_json(
            "https://text.pollinations.ai/openai",
            payload,
            {
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "LFELKEEA/1.0",
            },
            timeout,
        )
        message, finish_reason = _first_message(data)
        reply = _message_text(message)
        if not reply:
            raise _empty_reply_error(data, message, finish_reason)
        return reply

    api_key = (ai_config.get("api_key") or "").strip()
    if provider == "ollama":
        base_url = (ai_config.get("base_url") or "http://127.0.0.1:11434/v1").strip()
        model = model or "qwen2.5:7b"
        headers = {"Content-Type": "application/json; charset=utf-8"}
    else:
        base_url = (ai_config.get("base_url") or "https://api.openai.com/v1").strip()
        model = model or "gpt-4o-mini"
        if not api_key:
            raise AIError("还没有填写 API Key")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": 500,
    }
    if provider == "openai" and _is_deepseek(model):
        # DeepSeek 默认开启思考模式，思维链会占掉 max_tokens，
        # 长一点的输入（例如记忆整理）就容易只剩空 content。
        payload["reasoning_effort"] = "none"

    url = _endpoint(base_url)
    data, used_payload = _post_chat(url, payload, headers, timeout)
    message, finish_reason = _first_message(data)
    reply = _message_text(message)

    if not reply and finish_reason == "length":
        # 被截断时放宽一次上限，避免长输入直接判定为空回复。
        retry_payload = dict(used_payload)
        retry_payload["max_tokens"] = 1500
        try:
            retry_data, _ = _post_chat(url, retry_payload, headers, timeout)
            retry_message, retry_finish = _first_message(retry_data)
        except AIError:
            pass
        else:
            retry_reply = _message_text(retry_message)
            if retry_reply:
                return retry_reply
            data, message, finish_reason = retry_data, retry_message, retry_finish

    if not reply:
        raise _empty_reply_error(data, message, finish_reason)
    return reply


class AISignals(QObject):
    ok = Signal(str)
    fail = Signal(str)


class AIRequest:
    """在后台线程里请求 AI，结果通过 Qt 信号回到主线程。"""

    def __init__(self, ai_config: dict[str, Any], messages: list[dict[str, Any]]) -> None:
        self.ai_config = dict(ai_config)
        self.messages = [dict(item) for item in messages]
        self.signals = AISignals()
        self._cancelled = False

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self) -> None:
        self._cancelled = True

    def _run(self) -> None:
        try:
            reply = request_chat(self.ai_config, self.messages)
        except AIError as exc:
            if not self._cancelled:
                self.signals.fail.emit(str(exc))
        except Exception as exc:  # pragma: no cover
            if not self._cancelled:
                self.signals.fail.emit(f"未知错误：{exc}")
        else:
            if not self._cancelled:
                self.signals.ok.emit(reply)
