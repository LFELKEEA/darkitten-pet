from __future__ import annotations

"""直接读取桌宠自己的 AI 配置并发起对话。

为什么不走 ``api.chat``：
宿主会给回复做 800 字截断、max_tokens 固定 500，适合桌宠闲聊，却写不出一整个代码文件。
这里只复用用户已经配置好的 ``data/config.json → ai``，不做任何密钥落盘或日志输出。

安全约定：
- api_key 只在内存里存在，绝不写日志、绝不放进会话记录、绝不上传到配置之外的服务；
- 请求地址只来自用户自己的配置（OpenAI 兼容 / Ollama / 免费公共通道）。
"""

import json
import random
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
DEFAULT_MODELS = {
    "pollinations": "openai-fast",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5:7b",
}


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    provider: str = "pollinations"
    model: str = "openai-fast"
    base_url: str = ""
    api_key: str = ""
    timeout: int = 60
    temperature: float = 0.3


def _clamp_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


def load_llm_config(
    config_path: Path | str,
    *,
    model_override: str = "",
    temperature: float = 0.3,
) -> LLMConfig:
    data: dict[str, Any] = {}
    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = raw
    except (OSError, ValueError):
        data = {}

    ai = data.get("ai") if isinstance(data.get("ai"), dict) else {}
    provider = str(ai.get("provider", "pollinations") or "pollinations").strip().lower()
    if provider not in DEFAULT_MODELS:
        provider = "openai"
    model = str(model_override or ai.get("model") or DEFAULT_MODELS[provider]).strip()
    base_url = str(ai.get("base_url") or DEFAULT_BASE_URLS.get(provider, "")).strip()
    return LLMConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=str(ai.get("api_key") or "").strip(),
        timeout=_clamp_int(ai.get("timeout"), 90, 45, 180),
        temperature=float(temperature),
    )


def describe_config(config: LLMConfig) -> dict[str, Any]:
    """给界面看的摘要，不包含密钥。"""
    endpoint = config.base_url or DEFAULT_BASE_URLS.get(config.provider, "")
    return {
        "provider": config.provider,
        "model": config.model,
        "endpoint": endpoint,
        "key_configured": bool(config.api_key),
        "needs_key": config.provider == "openai" and not config.api_key,
    }


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("接口地址为空")
    if base.endswith("/chat/completions"):
        return base
    if not re.search(r"/v\d+(?:beta)?$", base):
        base += "/v1"
    return base + "/chat/completions"


def _http_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        raise LLMError(f"接口返回 {exc.code}。{detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"网络连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise LLMError("连接超时，模型没有在时限内响应") from exc
    except OSError as exc:
        raise LLMError(f"网络错误：{exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise LLMError("接口返回了无法解析的内容") from exc
    if not isinstance(data, dict):
        raise LLMError("接口返回格式不正确")
    return data


def _post(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """兼容接口不认识 reasoning_effort 时去掉它再试一次。"""
    try:
        return _http_json(url, payload, headers, timeout), payload
    except LLMError as exc:
        if "reasoning_effort" in payload and re.search(r"接口返回 (?:400|422)", str(exc)):
            fallback = dict(payload)
            fallback.pop("reasoning_effort", None)
            return _http_json(url, fallback, headers, timeout), fallback
        raise


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    text = str(content or "")
    if text.startswith("\ufeff"):
        text = text[1:]
    return text.strip()


def chat(
    config: LLMConfig,
    messages: list[dict[str, Any]],
) -> tuple[str, str]:
    """返回 (文本, finish_reason)。失败抛 LLMError。

    不发送 max_tokens：输出长度完全交给模型和接口自身的默认值/上限决定。
    """
    temperature = float(config.temperature)

    if config.provider == "pollinations":
        payload = {
            "model": config.model or "openai-fast",
            "messages": messages,
            "temperature": temperature,
            "private": True,
            "referrer": "lfelkeea-self-dev",
            "seed": random.randint(1, 999_999),
        }
        data = _http_json(
            "https://text.pollinations.ai/openai",
            payload,
            {
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "LFELKEEA-SelfDev/1.0",
            },
            config.timeout,
        )
        message, finish_reason = _first_message(data)
        text = _message_text(message)
        if not text:
            raise _empty_error(data, message, finish_reason)
        return text, finish_reason

    if config.provider == "ollama":
        base_url = config.base_url or DEFAULT_BASE_URLS["ollama"]
        headers = {"Content-Type": "application/json; charset=utf-8"}
    else:
        base_url = config.base_url or DEFAULT_BASE_URLS["openai"]
        if not config.api_key:
            raise LLMError("桌宠还没有配置 API Key，请先到「AI 与桌宠设置」里填好。")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {config.api_key}",
        }

    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
    }
    if config.provider == "openai" and "deepseek" in config.model.lower():
        # 思考模式会吃掉输出预算，代码任务里最容易表现成空回复。
        payload["reasoning_effort"] = "none"

    url = _endpoint(base_url)
    data, _used_payload = _post(url, payload, headers, config.timeout)
    message, finish_reason = _first_message(data)
    text = _message_text(message)

    if not text:
        raise _empty_error(data, message, finish_reason)
    return text, finish_reason


def _first_message(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    try:
        choice = data["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        detail = data.get("error") if isinstance(data, dict) else None
        if isinstance(detail, dict):
            detail = detail.get("message")
        raise LLMError(f"接口没有返回有效回复。{detail or ''}".strip()) from exc
    if not isinstance(message, dict):
        message = {}
    finish_reason = ""
    if isinstance(choice, dict):
        finish_reason = str(choice.get("finish_reason") or "")
    return message, finish_reason


def _empty_error(data: dict[str, Any], message: dict[str, Any], finish_reason: str) -> LLMError:
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
    hint = "。接口在自身输出上限处返回了空内容，可以重试，或把这一步拆成更小的动作" if finish_reason == "length" else ""
    return LLMError(f"接口返回了空回复{suffix}{hint}")
