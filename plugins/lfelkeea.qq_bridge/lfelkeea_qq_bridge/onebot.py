from __future__ import annotations

"""OneBot 11 HTTP 客户端与事件接收服务（只依赖标准库）。

SnowLuma 的 httpServers 提供 /<action> 形式的 HTTP API；httpClients 把事件
POST 到我们这里，并在设置了 accessToken 时附带 X-Signature（HMAC-SHA1）。
"""

import hashlib
import hmac
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit


class OneBotError(RuntimeError):
    pass


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [item for item in value if item is not None]
    return [value]


class OneBotClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 8.0) -> None:
        self.base_url = _normalize_base(base_url)
        self.token = str(token or "").strip()
        try:
            self.timeout = max(1.0, min(30.0, float(timeout)))
        except (TypeError, ValueError):
            self.timeout = 8.0

    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        name = str(action or "").strip().lstrip("/")
        if not name:
            raise OneBotError("缺少 OneBot 动作名。")
        url = f"{self.base_url}/{name}"
        body = json.dumps(params or {}, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "DarkittenQQBridge/1.0"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except OSError:
                pass
            raise OneBotError(f"{name} HTTP {exc.code} {detail}".strip()) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OneBotError(f"连不上 OneBot（{self.base_url}）：{exc}") from exc

        try:
            payload = json.loads(raw.decode("utf-8", "replace") or "{}")
        except ValueError as exc:
            raise OneBotError(f"{name} 返回的不是 JSON。") from exc
        if not isinstance(payload, dict):
            raise OneBotError(f"{name} 返回格式异常。")

        status = str(payload.get("status", "") or "").lower()
        retcode = payload.get("retcode")
        try:
            code = int(retcode) if retcode is not None else 0
        except (TypeError, ValueError):
            code = 0
        if status and status not in ("ok", "async", "success"):
            raise OneBotError(_error_text(payload, name))
        if code not in (0,):
            raise OneBotError(_error_text(payload, name))
        return payload.get("data")

    # ---------- 登录与连接 ----------

    def get_login_info(self) -> dict[str, Any]:
        data = self.call("get_login_info")
        return data if isinstance(data, dict) else {}

    def get_status(self) -> dict[str, Any]:
        data = self.call("get_status")
        return data if isinstance(data, dict) else {}

    def check(self) -> dict[str, Any]:
        """轻量连通性检查，优先用 get_login_info。"""
        return self.get_login_info()

    # ---------- 发消息 ----------

    def send_group(self, group_id: Any, segments: list[dict[str, Any]]) -> Any:
        gid = _as_id(group_id)
        if not gid:
            raise OneBotError("群号为空。")
        return self.call("send_group_msg", {"group_id": gid, "message": segments})

    def send_private(self, user_id: Any, segments: list[dict[str, Any]]) -> Any:
        uid = _as_id(user_id)
        if not uid:
            raise OneBotError("QQ 号为空。")
        return self.call("send_private_msg", {"user_id": uid, "message": segments})

    def send(self, conversation: str, segments: list[dict[str, Any]]) -> Any:
        kind, target = split_conversation(conversation)
        if kind == "group":
            return self.send_group(target, segments)
        return self.send_private(target, segments)

    # ---------- 历史消息 ----------

    def group_history(self, group_id: Any, count: int = 20) -> list[dict[str, Any]]:
        data = self.call(
            "get_group_msg_history",
            {"group_id": _as_id(group_id), "count": max(1, min(50, int(count)))},
        )
        return _history_list(data)

    def friend_history(self, user_id: Any, count: int = 20) -> list[dict[str, Any]]:
        data = self.call(
            "get_friend_msg_history",
            {"user_id": _as_id(user_id), "count": max(1, min(50, int(count)))},
        )
        return _history_list(data)

    # ---------- QQ 空间 ----------

    def publish_qzone(
        self,
        content: str,
        ugc_right: int = 4,
        images: list[str] | None = None,
        target_uins: list[str] | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "content": str(content or "")[:2000],
            "ugc_right": _ugc_right(ugc_right),
        }
        files = [str(item) for item in _as_list(images) if str(item).strip()]
        if files:
            params["images"] = files
        targets = [str(item) for item in _as_list(target_uins) if str(item).strip()]
        if targets:
            params["target_uins"] = targets
        return self.call("send_qzone_msg", params)

    def list_qzone(self, num: int = 20, target_uin: str = "") -> Any:
        params: dict[str, Any] = {"num": max(1, min(50, int(num)))}
        if str(target_uin or "").strip():
            params["target_uin"] = str(target_uin).strip()
        return self.call("get_qzone_msg_list", params)

    def delete_qzone(self, tid: str) -> Any:
        return self.call("delete_qzone_msg", {"tid": str(tid or "").strip()})

    def qzone_feeds(self, count: int = 10) -> Any:
        return self.call("get_qzone_feeds", {"count": max(1, min(30, int(count)))})


def _error_text(payload: dict[str, Any], action: str) -> str:
    for key in ("wording", "message", "msg", "error"):
        value = payload.get(key)
        if value:
            return f"{action} 失败：{value}"
    return f"{action} 失败：{payload.get('retcode')}"


def _history_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, dict):
        messages = data.get("messages")
    else:
        messages = data
    if isinstance(messages, list):
        return [item for item in messages if isinstance(item, dict)]
    return []


def _ugc_right(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 4
    return number if number in (1, 4, 16, 64, 128) else 4


def _normalize_base(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = "http://127.0.0.1:3000"
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


def _as_id(value: Any) -> str:
    return str(value if value is not None else "").strip()


def split_conversation(conversation: str) -> tuple[str, str]:
    text = str(conversation or "").strip()
    if ":" not in text:
        return ("group" if text.isdigit() and len(text) >= 5 else "private", text)
    kind, target = text.split(":", 1)
    kind = kind.strip().lower()
    if kind not in ("group", "private"):
        kind = "private"
    return kind, target.strip()


class EventServer:
    """接收 SnowLuma 事件推送的本地 HTTP 服务。"""

    def __init__(
        self,
        host: str,
        port: int,
        path: str,
        secret: str,
        on_event: Callable[[dict[str, Any]], None],
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.host = str(host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port or 0)
        self.path = _normalize_path(path)
        self.secret = str(secret or "")
        self.on_event = on_event
        self.on_error = on_error
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._auth_warned_at = 0.0

    @property
    def bound_port(self) -> int:
        server = self._server
        return int(server.server_address[1]) if server is not None else 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.bound_port}{self.path}"

    def start(self, port_attempts: int = 5) -> int:
        with self._lock:
            if self._server is not None:
                return self.bound_port
            last_error: Exception | None = None
            base_port = self.port
            attempts = max(1, int(port_attempts))
            for offset in range(attempts):
                try:
                    server = ThreadingHTTPServer(
                        (self.host, base_port + offset), self._make_handler()
                    )
                    break
                except (OSError, OverflowError) as exc:
                    last_error = exc
            else:
                raise OneBotError(f"事件端口启动失败：{last_error}")

            server.daemon_threads = True
            self._server = server
            self._thread = threading.Thread(
                target=server.serve_forever, name="qq-bridge-events", daemon=True
            )
            self._thread.start()
            return self.bound_port

    def stop(self) -> None:
        with self._lock:
            server, thread = self._server, self._thread
            self._server = None
            self._thread = None
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def _warn_bad_signature(self) -> None:
        """签名不对时给出提示，最多每分钟一条，避免刷屏。"""
        now = time.monotonic()
        if now - self._auth_warned_at < 60:
            return
        self._auth_warned_at = now
        if self.on_error is not None:
            self.on_error(
                "事件签名校验失败：SnowLuma HTTP 客户端的 Access Token 必须和插件「事件密钥」完全一致。"
            )

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "DarkittenQQBridge/1.0"
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != owner.path:
                    self._reply(404, {"status": "failed", "retcode": 404})
                    return
                self._reply(
                    200,
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"name": "darkitten-qq-bridge", "port": owner.bound_port},
                    },
                )

            def do_POST(self) -> None:  # noqa: N802
                if urlsplit(self.path).path != owner.path:
                    self._reply(404, {"status": "failed", "retcode": 404})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0") or 0)
                except (TypeError, ValueError):
                    length = 0
                if length <= 0 or length > 2 * 1024 * 1024:
                    self._reply(413, {"status": "failed", "retcode": 413})
                    return
                body = self.rfile.read(length)
                if owner.secret and not _verify_signature(owner.secret, body, self.headers):
                    owner._warn_bad_signature()
                    self._reply(401, {"status": "failed", "retcode": 401})
                    return
                try:
                    payload = json.loads(body.decode("utf-8", "replace"))
                except ValueError:
                    self._reply(400, {"status": "failed", "retcode": 400})
                    return
                if isinstance(payload, dict):
                    try:
                        owner.on_event(payload)
                    except Exception as exc:  # 事件处理不能拖垮 HTTP 线程
                        if owner.on_error is not None:
                            owner.on_error(f"事件处理出错：{exc}")
                self._reply(200, {"status": "ok", "retcode": 0})

            def _reply(self, code: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    self.close_connection = True

        return Handler


def _normalize_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "/onebot"
    if not text.startswith("/"):
        text = "/" + text
    return text


def _verify_signature(secret: str, body: bytes, headers: Any) -> bool:
    signature = str(headers.get("X-Signature", "") or "").strip().lower()
    # SnowLuma / OneBot 11 标准带 "sha1=" 前缀；也兼容直接发裸十六进制。
    if signature.startswith("sha1="):
        signature = signature[5:].strip()
    if not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return hmac.compare_digest(digest, signature)
