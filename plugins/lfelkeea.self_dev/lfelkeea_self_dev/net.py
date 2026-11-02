from __future__ import annotations

"""联网请求的共用实现：超时、大小上限、内网拦截、环境变量密钥。

连接器和联网搜索都走这里，避免各写一套 urllib 细节。
"""

import ipaddress
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (LFELKEEA-SelfDev/1.5; +https://github.com/LFELKEEA)"
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

_ENV_PROVIDER: Any = None


def set_env_provider(provider: Any) -> None:
    """注册本地环境变量仓库；传 None 恢复只读系统环境。"""
    global _ENV_PROVIDER
    _ENV_PROVIDER = provider


def lookup_env(name: str) -> str:
    """本地仓库优先，其次进程环境。仓库异常时退回系统变量。"""
    key = str(name or "")
    if _ENV_PROVIDER is not None:
        try:
            value = _ENV_PROVIDER(key)
        except Exception:
            value = ""
        if value:
            return str(value)
    return os.environ.get(key, "")


class NetworkError(Exception):
    pass


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = ""
    text: str = ""
    secrets: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Blob:
    url: str
    status: int
    content_type: str = ""
    data: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def decode_bytes(raw: bytes, content_type: str = "") -> str:
    match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type or "", re.IGNORECASE)
    encodings = []
    if match:
        encodings.append(match.group(1))
    encodings.extend(["utf-8", "gb18030", "cp1252", "latin-1"])
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def check_public_url(url: str, *, allow_private: bool = False) -> None:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https"):
        raise NetworkError("只支持 http/https 地址。")
    if not parsed.hostname:
        raise NetworkError("地址里没有主机名。")
    if allow_private:
        return
    host = parsed.hostname.strip().lower()
    if host in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise NetworkError("默认不允许访问本机和内网地址。")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise NetworkError("默认不允许访问本机和内网地址。")
    # 常见内网域名直接拦掉；不做 DNS 解析，避免请求前长时间阻塞。
    if host in ("metadata.google.internal", "instance-data"):
        raise NetworkError("默认不允许访问云主机元数据地址。")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool) -> None:
        self.allow_private = allow_private

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        check_public_url(newurl, allow_private=self.allow_private)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def resolve_env(value: Any, *, strict: bool = True) -> tuple[str, list[str]]:
    """把 ${ENV_NAME} 换成环境变量，同时返回解析出的密钥用于打码。"""
    text = str(value or "")
    secrets: list[str] = []
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        secret = lookup_env(name)
        if not secret:
            missing.append(name)
            return match.group(0)
        secrets.append(secret)
        return secret

    resolved = ENV_RE.sub(replace, text)
    if strict and missing:
        raise NetworkError("缺少环境变量：" + "、".join(sorted(set(missing))))
    return resolved, secrets


def resolve_headers(headers: Any, *, strict: bool = True) -> tuple[dict[str, str], list[str]]:
    if headers in (None, ""):
        return {}, []
    if not isinstance(headers, dict):
        raise NetworkError("headers 必须是对象。")
    result: dict[str, str] = {}
    secrets: list[str] = []
    for raw_key, raw_value in headers.items():
        key = str(raw_key or "").strip()
        if not key or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", key):
            raise NetworkError(f"header 名称不合法：{raw_key}")
        if key.lower() in ("host", "content-length", "connection"):
            continue
        value, found = resolve_env(raw_value, strict=strict)
        result[key] = value
        secrets.extend(found)
    return result, secrets


def http_request(
    url: str,
    *,
    method: str = "GET",
    headers: Any = None,
    body: Any = None,
    timeout: int = 25,
    max_bytes: int = MAX_BYTES,
    allow_private: bool = False,
) -> Response:
    target = str(url or "").strip()
    check_public_url(target, allow_private=allow_private)
    header_map, secrets = resolve_headers(headers, strict=True)
    header_map.setdefault("User-Agent", USER_AGENT)
    data: bytes | None = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            header_map.setdefault("Content-Type", "application/json; charset=utf-8")
        elif isinstance(body, bytes):
            data = body
        else:
            data = str(body).encode("utf-8")
    verb = str(method or "GET").upper()
    if verb not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        raise NetworkError(f"不支持的 HTTP 方法：{method}")
    request = urllib.request.Request(target, data=data, headers=header_map, method=verb)
    opener = urllib.request.build_opener(_SafeRedirect(allow_private))
    seconds = max(3, min(120, int(timeout or 25)))
    try:
        with opener.open(request, timeout=seconds) as response:
            final_url = response.geturl()
            check_public_url(final_url, allow_private=allow_private)
            content_type = str(response.headers.get("Content-Type", ""))
            raw = response.read(max_bytes + 1)
            status = int(getattr(response, "status", 200) or 200)
            header_dict = {str(k): str(v) for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read(max_bytes)
            header_dict = {str(k): str(v) for k, v in (exc.headers or {}).items()}
            content_type = str((exc.headers or {}).get("Content-Type", ""))
        except Exception:
            raw = b""
            header_dict = {}
            content_type = ""
        status = int(exc.code)
        final_url = str(exc.geturl() or target)
    except urllib.error.URLError as exc:
        raise NetworkError(f"网络请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise NetworkError("网络请求超时。") from exc
    except Exception as exc:
        raise NetworkError(f"网络请求失败：{exc}") from exc
    if len(raw) > max_bytes:
        text = decode_bytes(raw[:max_bytes], content_type) + "\n…（响应过大，已截断）"
    else:
        text = decode_bytes(raw, content_type)
    # 只把密钥留在调用方内存里；响应里若回显了密钥，调用方可再用 redact 处理。
    return Response(
        url=final_url,
        status=status,
        headers=header_dict,
        content_type=content_type,
        text=text,
        secrets=secrets,
    )


def download_bytes(
    url: str,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    timeout: int = 45,
    allow_private: bool = False,
) -> Blob:
    """下载二进制资源；用于技能附件。拒绝本机和内网地址，并检查重定向。"""
    target = str(url or "").strip()
    check_public_url(target, allow_private=allow_private)
    request = urllib.request.Request(
        target, headers={"User-Agent": USER_AGENT}, method="GET"
    )
    opener = urllib.request.build_opener(_SafeRedirect(allow_private))
    seconds = max(5, min(180, int(timeout or 45)))
    try:
        with opener.open(request, timeout=seconds) as response:
            final_url = response.geturl()
            check_public_url(final_url, allow_private=allow_private)
            content_type = str(response.headers.get("Content-Type", ""))
            data = response.read(max_bytes + 1)
            status = int(getattr(response, "status", 200) or 200)
    except urllib.error.HTTPError as exc:
        raise NetworkError(f"下载失败：HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"下载失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise NetworkError("下载超时。") from exc
    except Exception as exc:
        raise NetworkError(f"下载失败：{exc}") from exc
    if len(data) > max_bytes:
        raise NetworkError(f"文件超过上限（{max_bytes} B），已停止下载。")
    return Blob(url=final_url, status=status, content_type=content_type, data=data)


def resolve_address(host: str) -> list[str]:
    """留给需要严格 SSRF 检查的调用方；默认请求不解析 DNS。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    return sorted({item[4][0] for item in infos if item[4]})
