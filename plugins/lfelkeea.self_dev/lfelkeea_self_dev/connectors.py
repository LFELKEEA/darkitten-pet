from __future__ import annotations

"""连接器：HTTP API 和 MCP stdio 服务器。

- 定义保存在 ``data/dev/connectors.json``，密钥用 ``${ENV_NAME}`` 引用环境变量；
- HTTP 连接器支持路径、查询参数、请求体和 header 覆盖；
- MCP 连接器按需启动子进程，完成 initialize → tools/list → tools/call 后关闭；
- 所有密钥只在内存里解析，返回给模型的文本会做打码。
"""

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .net import NetworkError, http_request, resolve_env
from .resource_util import RESOURCE_ID_RE, ResourceError, atomic_write, inside, read_text, redact, stamp, trash_path

MCP_PROTOCOL = "2025-06-18"
CLIENT_INFO = {"name": "LFELKEEA-SelfDev", "version": "1.2.0"}
MAX_CONNECTOR_BYTES = 120_000
LEGACY_ENV_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _normalize_env_refs(value: Any) -> str:
    """把旧写法 {{NAME}} 统一成 ${NAME}。

    早期手工编辑或旧版说明里出现过双花括号，运行时只认 ${NAME}，
    旧写法会原样发给接口（例如 GitHub 直接 401）。这里在加载和保存时兜底迁移。
    """
    return LEGACY_ENV_RE.sub(r"${\1}", str(value))


def _as_timeout(value: Any, fallback: int = 45) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(5, min(180, number))


class ConnectorStore:
    def __init__(self, path: Path, workspace: Path, trash_root: Path | None = None) -> None:
        self.path = Path(path).resolve()
        self.workspace = Path(workspace).resolve()
        self.trash_root = Path(trash_root).resolve() if trash_root else self.path.parent / ".trash"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------ 读写

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(read_text(self.path))
        except (OSError, ValueError) as exc:
            raise ResourceError(f"connectors.json 无法解析：{exc}") from exc
        items = data.get("connectors") if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise ResourceError("connectors.json 里 connectors 必须是数组。")
        return [self._normalize(item) for item in items if isinstance(item, dict)]

    def _save_all(self, items: list[dict[str, Any]]) -> None:
        payload = {"version": 1, "connectors": items}
        atomic_write(
            self.path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

    def list(self) -> list[dict[str, Any]]:
        try:
            return self._load()
        except ResourceError:
            return []

    def get(self, connector_id: str) -> dict[str, Any]:
        value = str(connector_id or "").strip()
        for item in self._load():
            if item["id"] == value:
                return item
        raise ResourceError(f"连接器不存在：{value}")

    @staticmethod
    def public(item: dict[str, Any]) -> dict[str, Any]:
        """给界面和模型看的版本：密钥值只显示引用或 ***。"""
        data = dict(item)
        if data.get("type") == "http":
            headers = data.get("headers") or {}
            data["headers"] = {
                str(key): (value if "${" in str(value) else "***")
                for key, value in headers.items()
            }
        if data.get("type") == "mcp":
            data["env"] = {str(key): "***" for key in (data.get("env") or {})}
        return data

    def index_text(self) -> str:
        all_items = self.list()
        items = [item for item in all_items if item.get("enabled")]
        if not items:
            hidden = len(all_items)
            if hidden:
                return f"（还没有启用的连接器；资源界面有 {hidden} 个连接器处于关闭状态，需要时打开。）"
            return "（还没有连接器。连接器可以接 HTTP API 或 MCP 服务器，把外部能力带进来。）"
        lines: list[str] = []
        for item in items:
            kind = "HTTP" if item["type"] == "http" else "MCP"
            description = " ".join(str(item.get("description", "")).split())[:180]
            lines.append(f"- `{item['id']}`（{kind}）{item['name']}：{description}")
        return "\n".join(lines)

    def save(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError as exc:
                raise ResourceError(f"连接器不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ResourceError("连接器必须是 JSON 对象。")
        items = self._load()
        existing = next(
            (item for item in items if item["id"] == str(payload.get("id") or "").strip()),
            None,
        )
        data = dict(payload)
        if "enabled" not in data and existing is not None:
            data["enabled"] = existing.get("enabled", True)
        item = self._normalize(data)
        for index, current in enumerate(items):
            if current["id"] == item["id"]:
                items[index] = item
                break
        else:
            items.append(item)
        self._save_all(items)
        return item

    def set_enabled(self, connector_id: str, enabled: bool) -> dict[str, Any]:
        value = str(connector_id or "").strip()
        items = self._load()
        for item in items:
            if item["id"] == value:
                item["enabled"] = bool(enabled)
                self._save_all(items)
                return item
        raise ResourceError(f"连接器不存在：{value}")

    def seed_bundled(self, items: list[dict[str, Any]]) -> int:
        """把插件自带连接器补进 connectors.json；同 id 不覆盖。"""
        current = self._load()
        known = {item["id"] for item in current}
        created = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            connector_id = str(raw.get("id") or "").strip()
            if not connector_id or connector_id in known:
                continue
            payload = dict(raw)
            payload.setdefault("enabled", False)
            try:
                item = self._normalize(payload)
            except ResourceError:
                continue
            current.append(item)
            known.add(item["id"])
            created += 1
        if created:
            self._save_all(current)
        return created

    def delete(self, connector_id: str) -> str:
        value = str(connector_id or "").strip()
        items = self._load()
        kept = [item for item in items if item["id"] != value]
        if len(kept) == len(items):
            raise ResourceError(f"连接器不存在：{value}")
        backup = self.path.with_name(f"{self.path.name}.{stamp()}.bak")
        try:
            if self.path.is_file():
                backup.write_text(read_text(self.path), encoding="utf-8")
        except OSError:
            pass
        self._save_all(kept)
        return f"已删除连接器 {value}（原文件备份在 {backup.name}）。"

    def _normalize(self, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ResourceError("连接器必须是 JSON 对象。")
        connector_id = str(raw.get("id") or "").strip()
        if not RESOURCE_ID_RE.match(connector_id):
            raise ResourceError("连接器 id 只能用小写字母、数字、点、下划线、短横线，2-64 位。")
        kind = str(raw.get("type") or "http").strip().lower()
        if kind not in ("http", "mcp"):
            raise ResourceError("连接器 type 只能是 http 或 mcp。")
        item: dict[str, Any] = {
            "id": connector_id,
            "name": str(raw.get("name") or connector_id).strip()[:80],
            "type": kind,
            "description": str(raw.get("description") or "").strip()[:500],
            "enabled": bool(raw.get("enabled", True)),
            "timeout": _as_timeout(raw.get("timeout")),
        }
        if kind == "http":
            base_url = _normalize_env_refs(raw.get("base_url") or "").strip()
            if not re.match(r"^https?://", base_url, re.IGNORECASE) and not re.fullmatch(
                r"\$\{[A-Za-z_][A-Za-z0-9_]*\}", base_url
            ):
                raise ResourceError(
                    "HTTP 连接器需要 http/https 开头的 base_url，或整段用 ${ENV_NAME} 引用环境变量。"
                )
            headers = raw.get("headers") or {}
            if not isinstance(headers, dict):
                raise ResourceError("headers 必须是对象。")
            item.update(
                {
                    "base_url": base_url.rstrip("/"),
                    "headers": {
                        str(key): _normalize_env_refs(value)
                        for key, value in headers.items()
                    },
                    "allow_private": bool(raw.get("allow_private", False)),
                }
            )
        else:
            command = str(raw.get("command") or "").strip()
            if not command:
                raise ResourceError("MCP 连接器需要 command。")
            args = raw.get("args") or []
            if not isinstance(args, list) or not all(isinstance(part, (str, int, float)) for part in args):
                raise ResourceError("args 必须是字符串数组。")
            env = raw.get("env") or {}
            if not isinstance(env, dict):
                raise ResourceError("env 必须是对象。")
            cwd = str(raw.get("cwd") or "").strip()
            if cwd:
                relative = Path(cwd.replace("\\", "/"))
                if relative.is_absolute() or ".." in relative.parts:
                    raise ResourceError("MCP 的 cwd 必须是工作区内的相对路径。")
            item.update(
                {
                    "command": command,
                    "args": [str(part) for part in args],
                    "env": {
                        str(key): _normalize_env_refs(value)
                        for key, value in env.items()
                    },
                    "cwd": cwd,
                }
            )
        return item

    def http_call(
        self,
        connector_id: str,
        *,
        path: str = "",
        method: str = "GET",
        query: Any = None,
        body: Any = None,
        headers: Any = None,
        allow_private_network: bool = False,
    ) -> dict[str, Any]:
        connector = self.get(connector_id)
        if connector["type"] != "http":
            raise ResourceError(f"{connector_id} 不是 HTTP 连接器。")
        suffix = str(path or "").strip()
        if suffix.startswith(("http://", "https://")):
            raise ResourceError("path 只能是相对路径；完整地址请写进 base_url。")
        base_url, base_secrets = resolve_env(connector["base_url"], strict=True)
        url = base_url.rstrip("/") + ("/" + suffix.lstrip("/") if suffix else "")
        if query:
            if isinstance(query, dict):
                from urllib.parse import urlencode

                encoded = urlencode(query, doseq=True)
            else:
                encoded = str(query).lstrip("?")
            url += ("&" if "?" in url else "?") + encoded
        merged_headers = dict(connector.get("headers") or {})
        if headers:
            if not isinstance(headers, dict):
                raise ResourceError("headers 必须是对象。")
            merged_headers.update({str(key): str(value) for key, value in headers.items()})
        response = http_request(
            url,
            method=method,
            headers=merged_headers,
            body=body,
            timeout=connector.get("timeout", 45),
            allow_private=bool(connector.get("allow_private")) or allow_private_network,
        )
        text = response.text
        if "json" in response.content_type.lower():
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except ValueError:
                pass
        text = redact(text, [*base_secrets, *response.secrets])
        limit = 16000
        if len(text) > limit:
            text = text[:limit] + "\n…（响应超过 16000 字，已截断）"
        return {
            "connector": connector_id,
            "url": url,
            "status": response.status,
            "content_type": response.content_type,
            "text": text,
        }

    def mcp_tools(self, connector_id: str) -> dict[str, Any]:
        connector = self.get(connector_id)
        if connector["type"] != "mcp":
            raise ResourceError(f"{connector_id} 不是 MCP 连接器。")
        with MCPClient(connector, self.workspace) as client:
            info = client.initialize()
            tools = client.list_tools()
        return {
            "connector": connector_id,
            "server": info.get("serverInfo") if isinstance(info, dict) else {},
            "tools": tools,
        }

    def mcp_call(self, connector_id: str, tool: str, arguments: Any = None) -> dict[str, Any]:
        connector = self.get(connector_id)
        if connector["type"] != "mcp":
            raise ResourceError(f"{connector_id} 不是 MCP 连接器。")
        name = str(tool or "").strip()
        if not name:
            raise ResourceError("tool 不能为空。")
        args = arguments if isinstance(arguments, dict) else {}
        with MCPClient(connector, self.workspace) as client:
            client.initialize()
            result = client.call_tool(name, args)
            noise = client.stderr_tail()
        return {
            "connector": connector_id,
            "tool": name,
            "text": flatten_tool_result(result),
            "stderr": noise[-2000:],
        }

    def test(self, connector_id: str) -> str:
        connector = self.get(connector_id)
        if connector["type"] == "http":
            base_url, _secrets = resolve_env(connector["base_url"], strict=True)
            response = http_request(
                base_url,
                timeout=min(30, connector.get("timeout", 45)),
                headers=connector.get("headers") or {},
                allow_private=bool(connector.get("allow_private")),
            )
            return f"HTTP {response.status} · {response.content_type or '未知类型'} · {len(response.text)} 字"
        payload = self.mcp_tools(connector_id)
        tools = payload.get("tools") or []
        return f"MCP 已连接，发现 {len(tools)} 个工具。"


def flatten_tool_result(result: Any) -> str:
    if not isinstance(result, dict):
        return str(result)[:16000]
    parts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                parts.append(str(item))
                continue
            kind = str(item.get("type") or "")
            if kind == "text":
                parts.append(str(item.get("text") or ""))
            elif kind == "resource":
                resource = item.get("resource") or {}
                if isinstance(resource, dict):
                    parts.append(str(resource.get("text") or resource.get("uri") or ""))
                else:
                    parts.append(str(resource))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
    if result.get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"], ensure_ascii=False, indent=2))
    text = "\n".join(part for part in parts if part).strip()
    if not text:
        text = json.dumps(result, ensure_ascii=False, indent=2)
    if result.get("isError"):
        text = "MCP 工具返回错误：\n" + text
    if len(text) > 16000:
        text = text[:16000] + "\n…（结果超过 16000 字，已截断）"
    return text


class MCPClient:
    """一次调用一个进程，结束就关，避免留下常驻子进程。"""

    def __init__(self, connector: dict[str, Any], workspace: Path) -> None:
        self.connector = connector
        self.workspace = Path(workspace).resolve()
        self.proc: subprocess.Popen[str] | None = None
        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_lines: list[str] = []
        self._counter = 0
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._secrets: list[str] = []

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def start(self) -> None:
        command = str(self.connector.get("command") or "").strip()
        if not command:
            raise NetworkError("MCP 连接器没有 command。")
        executable = shutil.which(command) if not Path(command).is_file() else command
        if not executable:
            raise NetworkError(f"找不到 MCP 命令：{command}（确认已安装并在 PATH 里）。")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        for key, raw in (self.connector.get("env") or {}).items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(key)):
                raise NetworkError(f"MCP 环境变量名不合法：{key}")
            value, secrets = resolve_env(raw, strict=True)
            env[str(key)] = value
            self._secrets.extend(secrets)
        cwd = self.workspace
        relative = str(self.connector.get("cwd") or "").strip()
        if relative:
            cwd = (self.workspace / relative.replace("\\", "/")).resolve()
            if not inside(self.workspace, cwd):
                raise NetworkError("MCP 的 cwd 越界。")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
        argv = [executable, *[str(part) for part in (self.connector.get("args") or [])]]
        try:
            self.proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                creationflags=flags,
            )
        except OSError as exc:
            raise NetworkError(f"启动 MCP 服务器失败：{exc}") from exc
        self._reader = threading.Thread(target=self._read_stdout, name="selfdev-mcp-out", daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(
            target=self._read_stderr, name="selfdev-mcp-err", daemon=True
        )
        self._stderr_reader.start()

    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.stderr_lines.append("[stdout] " + line[:300])
                continue
            if isinstance(message, dict):
                self.queue.put(message)

    def _read_stderr(self) -> None:
        proc = self.proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self.stderr_lines.append(line.rstrip()[:500])
            if len(self.stderr_lines) > 200:
                del self.stderr_lines[:100]

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise NetworkError("MCP 进程没有启动。")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise NetworkError(f"写入 MCP 失败：{exc}") from exc

    def _request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._counter += 1
        request_id = self._counter
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)
        timeout = _as_timeout(self.connector.get("timeout"), 45)
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                message = self.queue.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self.proc is not None and self.proc.poll() is not None:
                    tail = " | ".join(self.stderr_lines[-5:])
                    raise NetworkError(
                        f"MCP 服务器提前退出（退出码 {self.proc.returncode}）"
                        + (f"：{tail}" if tail else "")
                    )
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message.get("error") or {}
                if isinstance(error, dict):
                    raise NetworkError(
                        f"MCP 错误 {error.get('code', '')}：{error.get('message', '未知错误')}"
                    )
                raise NetworkError(f"MCP 错误：{error}")
            return message.get("result") or {}
        raise NetworkError(f"MCP 在 {timeout} 秒内没有响应 {method}。")

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._send(payload)

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        )
        self._notify("notifications/initialized", {})
        return result if isinstance(result, dict) else {}

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        return [item for item in tools if isinstance(item, dict)] if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
        )
        return result if isinstance(result, dict) else {"content": [{"type": "text", "text": str(result)}]}

    def stderr_tail(self) -> str:
        return redact("\n".join(self.stderr_lines[-20:]), self._secrets)

    def close(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            # 只终止我们自己刚启动的子进程，绝不按进程名杀进程。
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
