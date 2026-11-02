from __future__ import annotations

"""自开发工坊的调度层：线程、审批、插件安装、会话记录。

约定：
- 只有 setup() 在主线程；Agent 循环在后台线程；
- 所有界面更新、api.say、插件管理器操作都通过 Qt 信号回到主线程；
- 没有打开工作台时，需要确认的操作一律拒绝（安全默认）。
"""

import threading
import time
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from .agent import Agent
from .exporter import ExportError, export_plugin, list_plugins
from .history import ChangeJournal
from .llm import LLMError, chat, describe_config, load_llm_config
from .paths import DevPaths
from .prompts import build_system_prompt, build_user_prompt
from .session import SessionLog
from .settings import normalize_settings
from .tools import TOOL_SPECS, ToolBox

APPROVAL_TIMEOUT = 180
ASK_TIMEOUT = 300
ENABLE_TIMEOUT = 30


class DevService(QObject):
    event = Signal(object)
    approval_requested = Signal(object)
    ask_requested = Signal(object)
    enable_requested = Signal(object)
    finished = Signal(object)

    def __init__(self, api: Any, paths: DevPaths | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.raw = self._read_settings()
        self.settings = normalize_settings(self.raw)
        self.paths = (paths or DevPaths.discover(self.settings["workspace_dir"])).ensure()
        self.catalog_seeded = self._seed_catalog()
        self.ui: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._session: SessionLog | None = None
        self._backlog: list[dict[str, Any]] = []
        self._pending: list[dict[str, Any]] = []
        self._running = False
        self._started_at = 0.0
        self._last_result: dict[str, Any] = {}
        self.last_session = ""
        self.approval_requested.connect(self._handle_approval)
        self.ask_requested.connect(self._handle_ask)
        self.enable_requested.connect(self._handle_enable)
        self.finished.connect(self._handle_finished)

    # ------------------------------------------------------------ 基础状态

    def _read_settings(self) -> dict[str, Any]:
        try:
            value = self.api.settings
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _seed_catalog(self) -> dict[str, int]:
        """把插件自带技能和连接器补进当前数据目录；失败不影响插件启动。"""
        try:
            from .catalog import seed_catalog

            return seed_catalog(self.paths)
        except Exception:
            return {"skills": 0, "connectors": 0}

    def reload_settings(self) -> dict[str, Any]:
        self.raw = self._read_settings()
        previous_workspace = self.paths.workspace
        self.settings = normalize_settings(self.raw)
        self.paths = DevPaths.discover(self.settings["workspace_dir"]).ensure()
        self.catalog_seeded = self._seed_catalog()
        if previous_workspace != self.paths.workspace:
            self._emit(
                {
                    "type": "info",
                    "text": f"工作区已切换到：{self.paths.workspace}",
                }
            )
        return dict(self.settings)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def started_at(self) -> float:
        return self._started_at

    def backlog(self) -> list[dict[str, Any]]:
        return list(self._backlog[-500:])

    def describe(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "workspace": str(self.paths.workspace),
            "dev_dir": str(self.paths.dev_dir),
            "plugins_dir": str(self.paths.plugins_dir),
            "plugin_folder": str(self.paths.plugin_folder),
            "session": self.last_session,
            "running": self._running,
            "provider": "",
            "model": "",
            "endpoint": "",
            "key_configured": False,
            "needs_key": False,
            "error": "",
        }
        try:
            config = load_llm_config(
                self.paths.config_path,
                model_override=self.settings["model_override"],
                temperature=self.settings["temperature"],
            )
            info.update(describe_config(config))
        except Exception as exc:
            info["error"] = str(exc)
        return info

    # ------------------------------------------------------------ 启动 / 停止

    def start_task(self, task: str) -> tuple[bool, str]:
        text = str(task or "").strip()
        if not text:
            return False, "先写一句要做什么。"
        if len(text) > 8000:
            return False, "任务描述太长了，请精简到 8000 字以内。"
        if self._running:
            return False, "上一轮还在跑，先等它结束或点停止。"

        info = self.describe()
        if info.get("needs_key"):
            return False, "桌宠还没有配置 API Key，请先到「AI 与桌宠设置」里填好。"

        session = SessionLog(self.paths.sessions, text)
        self._session = session
        self.last_session = str(session.path)
        self._stop = threading.Event()
        self._running = True
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=self._run_agent,
            args=(text, session, self._stop),
            name="selfdev-agent",
            daemon=True,
        )
        self._thread.start()
        return True, "已开始"

    def stop_task(self) -> bool:
        if not self._running:
            return False
        self._stop.set()
        for request in list(self._pending):
            request["result"] = False
            request["event"].set()
        self._emit({"type": "info", "text": "收到停止请求，正在收尾…"})
        return True

    def shutdown(self) -> None:
        self.stop_task()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self.ui = None

    # ------------------------------------------------------------ 主循环

    def _run_agent(self, task: str, session: SessionLog, stop: threading.Event) -> None:
        result: dict[str, Any] = {
            "ok": False,
            "summary": "",
            "steps": 0,
            "reason": "error",
        }
        started = time.time()
        settings = dict(self.settings)
        paths = self.paths
        toolbox: ToolBox | None = None
        try:
            config = load_llm_config(
                paths.config_path,
                model_override=settings["model_override"],
                temperature=settings["temperature"],
            )
            info = describe_config(config)
            self._emit(
                {
                    "type": "start",
                    "task": task,
                    "provider": info["provider"],
                    "model": info["model"],
                    "endpoint": info["endpoint"],
                    "workspace": str(paths.workspace),
                    "plugin_folder": str(paths.plugin_folder),
                    "session": str(session.path),
                }
            )
            if info["needs_key"]:
                raise LLMError("桌宠还没有配置 API Key，请先到「AI 与桌宠设置」里填好。")

            toolbox = ToolBox(
                workspace=paths.workspace,
                dev_dir=paths.dev_dir,
                plugins_dir=paths.plugins_dir,
                data_dir=paths.data_dir,
                references=paths.references,
                settings=settings,
                approver=self._approve,
                asker=self._ask,
                enable_plugin=self._enable_plugin,
                stop_event=stop,
                logger=self._log,
            )
            seeded = toolbox.skills.seed_defaults()
            if seeded:
                self._emit({"type": "info", "text": f"已初始化 {seeded} 个默认技能。"})
            knowledge_seeded = toolbox.knowledge.seed_defaults(
                paths.references, paths.plugin_folder / "README.md"
            )
            if knowledge_seeded:
                self._emit(
                    {"type": "info", "text": f"知识库已收录 {knowledge_seeded} 份插件参考资料。"}
                )
            tree = self._workspace_tree()
            knowledge = toolbox.knowledge.stats()
            system_prompt = build_system_prompt(
                str(paths.workspace),
                TOOL_SPECS,
                plugin_folder=str(paths.plugin_folder),
                data_dir=str(paths.data_dir),
                model=config.model,
                workspace_note=f"当前工作区顶层文件：{tree}",
                skills_index=toolbox.skills.index_text(),
                connectors_index=toolbox.connectors.index_text(),
                knowledge_stats=(
                    f"{knowledge['documents']} 个文档，{knowledge['chunks']} 个片段，"
                    f"约 {knowledge['chars']} 字。需要旧结论时先用 search_knowledge。"
                ),
            )
            user_prompt = build_user_prompt(task, tree)

            def chat_fn(messages: list[dict[str, Any]]) -> tuple[str, str]:
                if stop.is_set():
                    raise LLMError("已停止")
                return chat(config, messages)

            agent = Agent(
                chat_fn=chat_fn,
                toolbox=toolbox,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                emit=self._emit,
                stop_event=stop,
            )
            result = agent.run()
        except LLMError as exc:
            result = {"ok": False, "summary": str(exc), "steps": 0, "reason": "error"}
        except Exception as exc:
            detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            self._log(f"自开发任务异常：{detail}")
            result = {
                "ok": False,
                "summary": f"{type(exc).__name__}: {exc}",
                "steps": 0,
                "reason": "error",
            }
        finally:
            elapsed = time.time() - started
            if settings.get("auto_learn", True) and toolbox is not None:
                try:
                    summary = str(result.get("summary", "")).strip()
                    if summary and summary != "已被主人停止。":
                        first_line = next(
                            (line.strip() for line in task.splitlines() if line.strip()), task
                        )
                        note = (
                            f"任务：{task.strip()}\n\n"
                            f"结果：{'成功' if result.get('ok') else '未完成'}\n\n"
                            f"{summary}\n\n"
                            f"会话日志：{session.path.name}"
                        )
                        learned = toolbox.knowledge.add_document(
                            f"任务：{first_line[:80]}",
                            note,
                            source=f"session:{session.path.name}",
                            kind="session",
                        )
                        if not learned.get("existing"):
                            self._emit(
                                {
                                    "type": "info",
                                    "text": f"已把这次任务总结存入知识库（#{learned['id']}）。",
                                }
                            )
                except Exception as exc:
                    self._log(f"写入知识库失败：{exc}")
            self._running = False
            self._thread = None
            self._last_result = dict(result)
            payload = dict(result)
            payload.update(
                {
                    "type": "done",
                    "elapsed": round(elapsed, 1),
                    "session": str(session.path),
                    "workspace": str(self.paths.workspace),
                    "changed_files": [],
                }
            )
            self._emit(payload)
            session.close()
            self._session = None
            self.finished.emit(dict(result))

    # ------------------------------------------------------------ 事件与线程桥

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self._backlog.append(event)
            if len(self._backlog) > 800:
                del self._backlog[:200]
            session = self._session
            if session is not None:
                session.write(event)
            self.event.emit(event)
        except Exception:
            pass

    def _log(self, message: str) -> None:
        try:
            self.api.log(f"[self_dev] {message}")
        except Exception:
            pass

    def _approve(self, tool: str, title: str, summary: str, detail: str) -> bool:
        if self.ui is None:
            self._emit(
                {
                    "type": "info",
                    "text": f"需要确认的操作（{tool}）没有工作台可以弹窗，已按安全默认拒绝。",
                }
            )
            return False
        request = {
            "tool": tool,
            "title": title,
            "summary": summary,
            "detail": detail,
            "event": threading.Event(),
            "result": False,
        }
        self._pending.append(request)
        try:
            self.approval_requested.emit(request)
            if not request["event"].wait(APPROVAL_TIMEOUT):
                self._emit({"type": "info", "text": "确认超时，已拒绝这次操作。"})
                return False
            return bool(request.get("result"))
        finally:
            if request in self._pending:
                self._pending.remove(request)

    def _ask(self, question: str, options: list[str]) -> str:
        if self.ui is None:
            return "（主人没有打开工作台，暂时无法回答；请按最保守的方式继续，并在总结里说明这个待确认点。）"
        request = {
            "question": question,
            "options": list(options or []),
            "event": threading.Event(),
            "result": "",
        }
        self._pending.append(request)
        try:
            self.ask_requested.emit(request)
            if not request["event"].wait(ASK_TIMEOUT):
                return "（等待超时，主人没有回答；请按最保守的方式继续。）"
            return str(request.get("result") or "（主人没有回答）")
        finally:
            if request in self._pending:
                self._pending.remove(request)

    def _enable_plugin(self, plugin_id: str, enable: bool) -> tuple[bool, str]:
        if threading.current_thread() is threading.main_thread():
            return self._do_enable(plugin_id, enable)
        request = {
            "plugin_id": plugin_id,
            "enable": bool(enable),
            "event": threading.Event(),
            "result": (False, "主线程没有响应"),
        }
        self._pending.append(request)
        try:
            self.enable_requested.emit(request)
            if not request["event"].wait(ENABLE_TIMEOUT):
                return False, "插件管理器 30 秒内没有响应，请到「插件管理」手动启用。"
            return request.get("result") or (False, "未知错误")
        finally:
            if request in self._pending:
                self._pending.remove(request)

    def _handle_approval(self, request: dict[str, Any]) -> None:
        try:
            if self.ui is None:
                request["result"] = False
                return
            self.ui.ask_approval(request)
        except Exception as exc:
            self._log(f"确认弹窗异常：{exc}")
            request["result"] = False
        finally:
            request["event"].set()

    def _handle_ask(self, request: dict[str, Any]) -> None:
        try:
            if self.ui is None:
                request["result"] = "（主人没有打开工作台）"
                return
            self.ui.ask_question(request)
        except Exception as exc:
            self._log(f"提问弹窗异常：{exc}")
            request["result"] = "（提问失败，请按最保守的方式继续）"
        finally:
            request["event"].set()

    def _handle_enable(self, request: dict[str, Any]) -> None:
        try:
            request["result"] = self._do_enable(
                str(request.get("plugin_id", "")), bool(request.get("enable", True))
            )
        except Exception as exc:
            request["result"] = (False, f"启用插件失败：{exc}")
        finally:
            request["event"].set()

    def _do_enable(self, plugin_id: str, enable: bool) -> tuple[bool, str]:
        manager = getattr(self.api, "manager", None)
        if manager is None:
            return False, "当前环境没有插件管理器，请到「插件管理」手动启用。"
        try:
            manager.discover()
            info = manager.get(plugin_id)
            if info is None:
                return False, "插件管理器没有发现这个插件，请检查 plugin.json 是否合法。"
            if info.status == "运行中":
                ok = manager.reload_plugin(plugin_id)
                if ok:
                    return True, "已重载"
                return False, info.error or "重载失败，请查看插件日志"
            if enable:
                ok = manager.set_enabled(plugin_id, True)
                if ok:
                    return True, "已启用"
                return False, info.error or "启用失败，请查看插件日志"
            manager.set_enabled(plugin_id, False)
            return True, "未启用"
        except Exception as exc:
            return False, f"操作插件管理器失败：{exc}"

    # ------------------------------------------------------------ 还原与导出

    def _journal(self) -> ChangeJournal:
        return ChangeJournal(
            self.paths.dev_dir,
            self.paths.plugins_dir,
            self.paths.workspace,
            logger=self._log,
        )

    def change_summary(self) -> dict[str, Any]:
        try:
            return self._journal().summary()
        except Exception as exc:
            self._log(f"读取改动账本失败：{exc}")
            return {"files": [], "plugins": [], "count": 0, "updated_at": "", "error": str(exc)}

    def restore_all(self) -> dict[str, Any]:
        if self._running:
            return {"error": "任务还在运行，先等它结束或点停止。"}
        self._emit({"type": "info", "text": "开始一键还原：先把当前状态备份到 .backups/restore…"})
        try:
            result = self._journal().restore_all(
                self.paths.backups / "restore",
                skip_plugins={"lfelkeea.self_dev"},
            )
        except Exception as exc:
            self._log(f"一键还原异常：{exc}")
            self._emit({"type": "info", "text": f"一键还原失败：{exc}"})
            return {"error": str(exc)}
        changed = (
            len(result.get("restored_files", []))
            + len(result.get("removed_files", []))
            + len(result.get("restored_plugins", []))
            + len(result.get("removed_plugins", []))
        )
        errors = result.get("errors") or []
        lines = [
            f"还原文件 {len(result.get('restored_files', []))} 个、移除新增文件 "
            f"{len(result.get('removed_files', []))} 个、还原插件 "
            f"{len(result.get('restored_plugins', []))} 个、移除新增插件 "
            f"{len(result.get('removed_plugins', []))} 个。",
            f"还原前的状态已备份到：{result.get('backup_dir', '')}",
        ]
        if result.get("skipped_plugins"):
            lines.append("跳过（不还原自身）：" + "、".join(result["skipped_plugins"]))
        if errors:
            lines.append(f"有 {len(errors)} 个问题：" + "；".join(errors[:5]))
        for line in lines:
            self._emit({"type": "info", "text": line})
        try:
            storage = self.api.storage
            storage["last_restore"] = str(result.get("stamp", ""))
            storage["last_restore_count"] = changed
            self.api.save_storage()
        except Exception:
            pass
        return result

    def installed_plugins(self) -> list[dict[str, Any]]:
        try:
            return list_plugins(self.paths.plugins_dir)
        except Exception as exc:
            self._log(f"读取插件列表失败：{exc}")
            return []

    def export_plugin(self, plugin_id: str, destination: str) -> dict[str, Any]:
        try:
            result = export_plugin(self.paths.plugins_dir, plugin_id, destination)
        except ExportError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            self._log(f"导出插件异常：{exc}")
            return {"error": str(exc)}
        self._emit(
            {
                "type": "info",
                "text": f"已导出 {result['name']} v{result['version']}："
                f"{result['files']} 个文件，{result['zip_bytes']} B → {result['path']}",
            }
        )
        return result

    def _handle_finished(self, result: dict[str, Any]) -> None:
        try:
            if self.settings.get("notify_on_finish", True):
                if result.get("ok"):
                    summary = str(result.get("summary", "")).strip().replace("\n", " ")
                    self.api.say(f"写好啦。{summary[:90]}", 9000)
                    self.api.action("nod")
                else:
                    reason = str(result.get("summary", "")).strip().replace("\n", " ")
                    self.api.say(f"这次没做成……{reason[:80]}", 7000)
        except Exception:
            pass
        try:
            storage = self.api.storage
            storage["last_session"] = self.last_session
            storage["last_ok"] = bool(result.get("ok"))
            storage["last_steps"] = int(result.get("steps", 0) or 0)
            self.api.save_storage()
        except Exception:
            pass
        self._log(
            f"任务结束：ok={result.get('ok')} steps={result.get('steps')} "
            f"reason={result.get('reason')} session={self.last_session}"
        )

    # ------------------------------------------------------------ 辅助

    def _workspace_tree(self) -> str:
        lines: list[str] = []
        try:
            entries = sorted(
                self.paths.workspace.iterdir(),
                key=lambda item: (not item.is_dir(), item.name.lower()),
            )
        except OSError:
            return ""
        for item in entries[:40]:
            lines.append(f"{'[D]' if item.is_dir() else '[F]'} {item.name}")
        if len(entries) > 40:
            lines.append(f"…还有 {len(entries) - 40} 项")
        return "、".join(lines) if lines else "（空）"

    def activity_hint(self) -> str:
        if not self._running:
            return ""
        elapsed = int(time.time() - self._started_at)
        return f"正在自开发（已 {elapsed} 秒）"
