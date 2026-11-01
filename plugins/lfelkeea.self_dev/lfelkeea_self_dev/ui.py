from __future__ import annotations

"""自开发工作台：任务输入、实时步骤、审批弹窗、设置。

界面只负责展示和收集决定，真正的执行在 service 的后台线程里。
"""

import html
import json
import time
from typing import Any

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

BG = "#171214"
CARD = "#21181c"
CARD_2 = "#2b1f24"
BORDER = "#432a31"
ACCENT = "#bf2b35"
ACCENT_LIGHT = "#e0525b"
TEXT = "#f6ecef"
MUTED = "#a58f95"
OK = "#8ed6a2"
WARN = "#e0b25b"
ERROR = "#e28189"
TOOL = "#8fc7d9"
FONT = "Microsoft YaHei UI"
MONO = "Consolas"

STYLE = f"""
QDialog {{ background: {BG}; }}
QLabel {{ color: {TEXT}; font-family: "{FONT}"; font-size: 12px; background: transparent; }}
QLabel#title {{ font-size: 18px; font-weight: 700; }}
QLabel#subtitle {{ color: {MUTED}; font-size: 11px; }}
QLabel#section {{ color: {MUTED}; font-size: 11px; letter-spacing: 1px; }}
QLabel#muted {{ color: {MUTED}; font-size: 11px; }}
QLabel#warn {{ color: {WARN}; font-size: 11px; }}
QLabel#bad {{ color: {ERROR}; font-size: 11px; }}
QLabel#ok {{ color: {OK}; font-size: 11px; }}
QFrame#panel {{
    background-color: {CARD}; border: 1px solid {BORDER}; border-radius: 10px;
}}
QFrame#header {{ background: transparent; border: none; }}
QPlainTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 6px 8px; font-family: "{FONT}"; font-size: 12px;
    selection-background-color: {ACCENT};
}}
QPlainTextEdit {{ padding: 8px; }}
QComboBox QAbstractItemView {{
    background: {CARD_2}; color: {TEXT}; selection-background-color: {ACCENT};
}}
QCheckBox {{ color: {TEXT}; font-family: "{FONT}"; font-size: 12px; spacing: 7px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {BORDER}; background: {CARD_2};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT_LIGHT}; }}
QPushButton {{
    background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 6px 14px; font-family: "{FONT}"; font-size: 12px;
}}
QPushButton:hover {{ border-color: {ACCENT_LIGHT}; }}
QPushButton:disabled {{ color: #6f5c62; border-color: #33242a; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #fff; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_LIGHT}; }}
QPushButton#primary:disabled {{ background: #4a2b30; border-color: #4a2b30; color: #a58f95; }}
QPushButton#ghost {{ background: transparent; color: {MUTED}; border-color: transparent; }}
QPushButton#ghost:hover {{ color: {TEXT}; background: {CARD_2}; }}
QTextBrowser {{
    background: #141013; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; padding: 8px; font-family: "{FONT}"; font-size: 12px;
    selection-background-color: {ACCENT};
}}
QListWidget {{
    background: #141013; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; font-family: "{MONO}"; font-size: 11px; padding: 4px;
}}
QListWidget::item {{ padding: 3px 4px; }}
QSplitter::handle {{ background: transparent; width: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #4a333a; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #63434b; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""

TEMPLATES = [
    ("自定义任务", ""),
    (
        "做一个定时提醒插件",
        "给桌宠做一个新插件：每隔 45 分钟提醒我喝水，气泡显示 6 秒；"
        "用 PySide6 写一个简单的设置面板，可以改间隔和提醒语；"
        "写完后检查语法并告诉我怎么验证。",
    ),
    (
        "做一个桌面便签插件",
        "给桌宠做一个桌面便签插件：右键插件设置里可以新建、删除、勾选完成便签，"
        "数据存到插件自己的 storage；同时让桌宠在启动时用一句话提醒我最重要的一条。",
    ),
    (
        "修一个插件的报错",
        "我想修复一个桌宠插件的报错。先读 data/plugins 下相关插件和 data/plugins.log，"
        "找到根因，做最小修改，然后检查语法并说明验证方法。",
    ),
    (
        "写个小工具并自测",
        "在开发区里写一个 Python 小工具：扫描工作区里所有 Markdown 文件，"
        "统计标题数量和最长的一篇文章，输出一个简短报告；写完后实际运行一次并给出真实输出。",
    ),
]


class DevStudioDialog(QDialog):
    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._files: list[str] = []
        self._last_step = 0
        self._max_steps = 0
        self._started_at = 0.0
        self.setWindowTitle("LFELKEEA · 自开发工坊")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )
        self.resize(1080, 780)
        self.setMinimumSize(920, 640)
        self.setStyleSheet(STYLE)
        self._build()
        self.service.event.connect(self._on_event)
        self.service.ui = self
        self.destroyed.connect(self._detach)
        self._replay_backlog()
        self._refresh_header()
        self._sync_running()

        self._ticker = QTimer(self)
        self._ticker.setInterval(1000)
        self._ticker.timeout.connect(self._tick)
        self._ticker.start()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        header = QFrame()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("自开发工坊")
        title.setObjectName("title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("subtitle")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
        header_layout.addLayout(title_box, 1)
        self.ai_label = QLabel("")
        self.ai_label.setObjectName("muted")
        self.ai_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(self.ai_label)
        outer.addWidget(header)

        task_panel = QFrame()
        task_panel.setObjectName("panel")
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(12, 10, 12, 10)
        task_layout.setSpacing(6)

        task_head = QHBoxLayout()
        task_head.setSpacing(8)
        section = QLabel("要她做什么")
        section.setObjectName("section")
        task_head.addWidget(section)
        task_head.addStretch(1)
        self.template_box = QComboBox()
        self.template_box.setFixedWidth(210)
        for label, _value in TEMPLATES:
            self.template_box.addItem(label)
        self.template_box.currentIndexChanged.connect(self._apply_template)
        task_head.addWidget(self.template_box)
        task_layout.addLayout(task_head)

        self.task_edit = QPlainTextEdit()
        self.task_edit.setPlaceholderText(
            "例如：给桌宠做一个番茄钟插件，并且写完后自己检查语法、跑一遍验证……"
        )
        self.task_edit.setFixedHeight(84)
        task_layout.addWidget(self.task_edit)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self._stop)
        self.clear_btn = QPushButton("清空日志")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.clicked.connect(self._clear_log)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.clicked.connect(self._open_settings)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addStretch(1)
        actions.addWidget(self.clear_btn)
        actions.addWidget(self.settings_btn)
        task_layout.addLayout(actions)
        outer.addWidget(task_panel)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        log_panel = QFrame()
        log_panel.setObjectName("panel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(10, 8, 10, 8)
        log_layout.setSpacing(6)
        log_head = QHBoxLayout()
        log_title = QLabel("执行过程")
        log_title.setObjectName("section")
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("muted")
        log_head.addWidget(log_title)
        log_head.addStretch(1)
        log_head.addWidget(self.status_label)
        log_layout.addLayout(log_head)
        self.log_view = QTextBrowser()
        self.log_view.setOpenExternalLinks(False)
        self.log_view.document().setMaximumBlockCount(2500)
        log_layout.addWidget(self.log_view, 1)
        splitter.addWidget(log_panel)

        side = QFrame()
        side.setObjectName("panel")
        side.setMinimumWidth(260)
        side.setMaximumWidth(340)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 10, 12, 10)
        side_layout.setSpacing(8)

        side_layout.addWidget(self._section("模型"))
        self.model_label = QLabel("")
        self.model_label.setWordWrap(True)
        self.model_label.setObjectName("muted")
        side_layout.addWidget(self.model_label)

        side_layout.addWidget(self._section("工作区"))
        self.workspace_label = QLabel("")
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setObjectName("muted")
        side_layout.addWidget(self.workspace_label)
        open_ws = QPushButton("打开开发区")
        open_ws.clicked.connect(self._open_workspace)
        side_layout.addWidget(open_ws)

        side_layout.addWidget(self._section("本次改动"))
        self.files_list = QListWidget()
        self.files_list.setMinimumHeight(90)
        side_layout.addWidget(self.files_list, 1)

        side_layout.addWidget(self._section("会话日志"))
        self.session_label = QLabel("")
        self.session_label.setWordWrap(True)
        self.session_label.setObjectName("muted")
        side_layout.addWidget(self.session_label)
        open_session = QPushButton("打开日志目录")
        open_session.clicked.connect(self._open_sessions)
        side_layout.addWidget(open_session)

        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        outer.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.footer_label = QLabel("")
        self.footer_label.setObjectName("muted")
        footer.addWidget(self.footer_label, 1)
        hint = QLabel("命令执行和新插件启用默认需要确认 · 所有改动自动备份")
        hint.setObjectName("muted")
        footer.addWidget(hint)
        outer.addLayout(footer)

    def _section(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("section")
        return label

    # ------------------------------------------------------------ 交互

    def _apply_template(self, index: int) -> None:
        if index <= 0 or index >= len(TEMPLATES):
            return
        text = TEMPLATES[index][1]
        if text and not self.task_edit.toPlainText().strip():
            self.task_edit.setPlainText(text)

    def _start(self) -> None:
        if self.service.running:
            return
        text = self.task_edit.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "还差一句任务", "先写下要她做什么，例如“做一个定时提醒插件”。")
            return
        ok, message = self.service.start_task(text)
        if not ok:
            QMessageBox.warning(self, "没能开始", message)
            return
        self._files.clear()
        self.files_list.clear()
        self.log_view.clear()
        self._started_at = time.time()
        self._last_step = 0
        self._sync_running()

    def _stop(self) -> None:
        if self.service.stop_task():
            self.status_label.setText("正在停止…")
            self.stop_btn.setEnabled(False)

    def _clear_log(self) -> None:
        if self.service.running:
            QMessageBox.information(self, "还在运行", "等这一轮结束再清空，或者先点停止。")
            return
        self.log_view.clear()
        self._files.clear()
        self.files_list.clear()
        self._sync_running()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.service, self)
        dialog.exec()
        self._refresh_header()

    def _open_workspace(self) -> None:
        self._open_path(self.service.paths.workspace)

    def _open_sessions(self) -> None:
        self._open_path(self.service.paths.sessions)

    def _open_path(self, path: Any) -> None:
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            pass

    # ------------------------------------------------------------ 状态

    def _refresh_header(self) -> None:
        info = self.service.describe()
        self.subtitle.setText(
            f"工作区 {info['workspace']} · {info['provider'] or '未配置'} / {info['model'] or '未知模型'}"
        )
        if info.get("needs_key"):
            self.ai_label.setText("还没有 API Key")
            self.ai_label.setObjectName("bad")
        elif info.get("error"):
            self.ai_label.setText(info["error"][:60])
            self.ai_label.setObjectName("bad")
        else:
            self.ai_label.setText(
                f"{info['provider']} / {info['model']} · 上限 {info.get('max_tokens', 0)} tokens"
            )
            self.ai_label.setObjectName("muted")
        self.ai_label.style().unpolish(self.ai_label)
        self.ai_label.style().polish(self.ai_label)

        self.model_label.setText(
            f"{info['provider']} / {info['model']}\n{info['endpoint']}"
            + ("\n未配置密钥" if info.get("needs_key") else "")
        )
        self.workspace_label.setText(info["workspace"])
        self.session_label.setText(info.get("session") or "（还没有运行记录）")
        self._sync_running()

    def _sync_running(self) -> None:
        running = self.service.running
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        if not running:
            self.start_btn.setText("开始")
            self.status_label.setText("就绪")
            self.footer_label.setText(
                f"最多 {self.service.settings['max_steps']} 步 · 模型输出上限 "
                f"{self.service.settings['max_tokens']} tokens · 工作区 {self.service.paths.workspace}"
            )

    def _tick(self) -> None:
        if not self.service.running:
            return
        elapsed = int(time.time() - self._started_at) if self._started_at else 0
        step = f"第 {self._last_step}/{self._max_steps} 步 · " if self._last_step else ""
        self.status_label.setText(f"{step}{elapsed // 60:02d}:{elapsed % 60:02d}")

    # ------------------------------------------------------------ 事件渲染

    def _on_event(self, event: dict[str, Any]) -> None:
        try:
            self._render(event)
        except Exception:
            pass

    def _replay_backlog(self) -> None:
        for event in self.service.backlog()[-300:]:
            self._on_event(event)

    def _render(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type", ""))
        escape = html.escape

        if kind == "start":
            self._max_steps = int(event.get("max_steps", 0) or 0)
            self._started_at = time.time()
            self._append(
                f'<div style="color:{ACCENT_LIGHT};font-weight:700;margin-top:6px;">'
                f"开始任务</div>"
                f'<div style="color:{TEXT};">{escape(str(event.get("task", "")))}</div>'
                f'<div style="color:{MUTED};font-size:11px;">{escape(str(event.get("provider", "")))} / '
                f'{escape(str(event.get("model", "")))} · 工作区 {escape(str(event.get("workspace", "")))}</div>'
            )
            self._sync_running()
            return

        if kind == "step":
            self._last_step = int(event.get("step", 0) or 0)
            self._max_steps = int(event.get("max", self._max_steps) or self._max_steps)
            self._append(
                f'<div style="color:{BORDER};margin:8px 0 2px 0;">'
                f'<span style="color:{MUTED};font-size:11px;">— 第 {self._last_step} / {self._max_steps} 步 —</span></div>'
            )
            return

        if kind == "thought":
            text = escape(str(event.get("text", "")))
            self._append(
                f'<div style="color:{MUTED};font-style:italic;margin:2px 0;">{text}</div>'
            )
            return

        if kind == "tool":
            name = escape(str(event.get("name", "")))
            args = self._short_args(event.get("args"))
            self._append(
                f'<div style="margin:4px 0 1px 0;"><span style="color:{TOOL};font-family:{MONO};font-weight:700;">'
                f"&rarr; {name}</span> <span style=\"color:{MUTED};font-family:{MONO};font-size:11px;\">"
                f"{escape(args)}</span></div>"
            )
            return

        if kind == "result":
            ok = bool(event.get("ok"))
            color = OK if ok else ERROR
            mark = "✓" if ok else "✗"
            name = escape(str(event.get("name", "")))
            preview = escape(str(event.get("preview", "")))
            self._append(
                f'<div style="color:{color};font-family:{MONO};font-size:11px;margin-bottom:6px;">'
                f"{mark} {name} · {int(event.get('chars', 0) or 0)} 字</div>"
                f'<pre style="color:{TEXT};background:#1a1418;border:1px solid {BORDER};border-radius:6px;'
                f'padding:6px;margin:0 0 6px 0;white-space:pre-wrap;font-family:{MONO};font-size:11px;">'
                f"{preview}</pre>"
            )
            files = event.get("files") or []
            if isinstance(files, list):
                for item in files:
                    text = str(item)
                    if text and text not in self._files:
                        self._files.append(text)
                        self.files_list.addItem(text)
            return

        if kind == "info":
            text = escape(str(event.get("text", "")))
            self._append(f'<div style="color:{WARN};font-size:11px;margin:3px 0;">{text}</div>')
            return

        if kind == "final":
            text = escape(str(event.get("text", "")))
            self._append(
                f'<div style="color:{OK};margin:8px 0 2px 0;font-weight:700;">完成</div>'
                f'<div style="color:{TEXT};">{text}</div>'
            )
            return

        if kind == "done":
            ok = bool(event.get("ok"))
            color = OK if ok else ERROR
            title = "任务完成" if ok else "任务未完成"
            summary = escape(str(event.get("summary", "")))
            session = escape(str(event.get("session", "")))
            elapsed = event.get("elapsed", 0)
            self._append(
                f'<div style="border-top:1px solid {BORDER};margin-top:10px;padding-top:8px;">'
                f'<span style="color:{color};font-weight:700;">{title}</span>'
                f'<span style="color:{MUTED};font-size:11px;"> · {elapsed}s · {int(event.get("steps", 0) or 0)} 步</span>'
                f'<div style="color:{TEXT};margin-top:4px;">{summary}</div>'
                f'<div style="color:{MUTED};font-size:10px;margin-top:4px;">{session}</div></div>'
            )
            self.session_label.setText(session or "（无）")
            self.status_label.setText("完成" if ok else "未完成")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.footer_label.setText(
                f"本次 {int(event.get('steps', 0) or 0)} 步 · {elapsed}s · 改动 {len(self._files)} 个文件"
            )
            return

        text = escape(str(event.get("text", "")))
        self._append(f'<div style="color:{MUTED};font-size:11px;">{text}</div>')

    def _short_args(self, args: Any) -> str:
        try:
            text = json.dumps(args, ensure_ascii=False, default=str)
        except Exception:
            text = str(args)
        text = " ".join(text.split())
        return text if len(text) <= 300 else text[:300] + "…"

    def _append(self, html_text: str) -> None:
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_text + "<br>")
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    # ------------------------------------------------------------ 弹窗

    def ask_approval(self, request: dict[str, Any]) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(str(request.get("title") or "需要确认"))
        box.setText(str(request.get("summary") or "代理请求执行一个操作。"))
        box.setInformativeText("允许这一次吗？拒绝后她会换一种方式继续。")
        detail = str(request.get("detail") or "")
        if detail:
            box.setDetailedText(detail)
        allow = box.addButton("允许", QMessageBox.ButtonRole.AcceptRole)
        deny = box.addButton("拒绝", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(deny)
        box.exec()
        request["result"] = box.clickedButton() is allow

    def ask_question(self, request: dict[str, Any]) -> None:
        question = str(request.get("question") or "代理想确认一件事。")
        options = request.get("options") or []
        if options:
            question += "\n\n可选项：\n" + "\n".join(f"- {item}" for item in options)
        text, ok = QInputDialog.getMultiLineText(self, "代理提问", question, "")
        request["result"] = text.strip() if ok and text.strip() else "（主人没有回答）"

    def _detach(self) -> None:
        if getattr(self.service, "ui", None) is self:
            self.service.ui = None


class SettingsDialog(QDialog):
    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("自开发工坊 · 设置")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(620, 640)
        self.setStyleSheet(STYLE)
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("设置")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "自开发工坊直接使用桌宠「AI 与桌宠设置」里的接口和密钥；这里只覆盖模型名、输出上限和工具行为。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        panel = QFrame()
        panel.setObjectName("panel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        form.addWidget(self._row("审批模式", self._approval_combo()))

        self.shell_combo = QComboBox()
        for value, label in (
            ("auto", "自动（Windows 优先 Git Bash）"),
            ("bash", "Git Bash"),
            ("cmd", "cmd.exe"),
            ("powershell", "PowerShell"),
            ("sh", "/bin/sh"),
            ("off", "禁止执行命令"),
        ):
            self.shell_combo.addItem(label, value)
        form.addWidget(self._row("命令解释器", self.shell_combo))

        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(4, 60)
        form.addWidget(self._row("最多步数", self.steps_spin))

        self.tokens_spin = QSpinBox()
        self.tokens_spin.setRange(512, 8192)
        self.tokens_spin.setSingleStep(512)
        form.addWidget(self._row("单次输出上限", self.tokens_spin))

        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 1.5)
        self.temp_spin.setSingleStep(0.1)
        form.addWidget(self._row("温度", self.temp_spin))

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("留空则使用桌宠当前模型")
        form.addWidget(self._row("覆盖模型名", self.model_edit))

        self.workspace_edit = QLineEdit()
        self.workspace_edit.setPlaceholderText("留空 = data/dev/workspace")
        browse = QPushButton("选择…")
        browse.clicked.connect(self._browse_workspace)
        workspace_row = QHBoxLayout()
        workspace_row.setSpacing(6)
        workspace_row.addWidget(self.workspace_edit, 1)
        workspace_row.addWidget(browse)
        form.addWidget(self._row("工作区目录", workspace_row))

        self.allow_network = QCheckBox("允许联网读取文档（fetch_url）")
        self.allow_private = QCheckBox("允许访问本机和内网地址")
        self.allow_self = QCheckBox("允许修改自开发工坊自身（高风险）")
        self.auto_enable = QCheckBox("新插件通过检查后自动启用")
        self.notify = QCheckBox("任务结束让桌宠说一句")
        form.addWidget(self.allow_network)
        form.addWidget(self.allow_private)
        form.addWidget(self.allow_self)
        form.addWidget(self.auto_enable)
        form.addWidget(self.notify)

        layout.addWidget(panel)

        self.ai_info = QLabel("")
        self.ai_info.setObjectName("muted")
        self.ai_info.setWordWrap(True)
        layout.addWidget(self.ai_info)

        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _row(self, label: str, widget: Any) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        text = QLabel(label)
        text.setMinimumWidth(110)
        layout.addWidget(text)
        if isinstance(widget, QHBoxLayout):
            layout.addLayout(widget, 1)
        else:
            layout.addWidget(widget, 1)
        return holder

    def _approval_combo(self) -> QComboBox:
        self.approval_combo = QComboBox()
        for value, label in (
            ("confirm_danger", "只确认危险操作（推荐）"),
            ("auto", "全部自动执行"),
            ("confirm_all", "每一步都确认"),
        ):
            self.approval_combo.addItem(label, value)
        return self.approval_combo

    def _load(self) -> None:
        settings = self.service.settings
        index = self.approval_combo.findData(settings["approval_mode"])
        self.approval_combo.setCurrentIndex(max(0, index))
        index = self.shell_combo.findData(settings["shell_mode"])
        self.shell_combo.setCurrentIndex(max(0, index))
        self.steps_spin.setValue(int(settings["max_steps"]))
        self.tokens_spin.setValue(int(settings["max_tokens"]))
        self.temp_spin.setValue(float(settings["temperature"]))
        self.model_edit.setText(settings["model_override"])
        self.workspace_edit.setText(settings["workspace_dir"])
        self.allow_network.setChecked(bool(settings["allow_network"]))
        self.allow_private.setChecked(bool(settings["allow_private_network"]))
        self.allow_self.setChecked(bool(settings["allow_self_modify"]))
        self.auto_enable.setChecked(bool(settings["auto_enable_plugin"]))
        self.notify.setChecked(bool(settings["notify_on_finish"]))

        info = self.service.describe()
        self.ai_info.setText(
            f"当前接口：{info['provider']} / {info['model']}\n"
            f"地址：{info['endpoint']}\n"
            f"密钥：{'已配置' if info['key_configured'] else '未配置（Ollama 和免费通道不需要）'}"
        )

    def _browse_workspace(self) -> None:
        start = self.workspace_edit.text().strip() or str(self.service.paths.workspace)
        path = QFileDialog.getExistingDirectory(self, "选择工作区目录", start)
        if path:
            self.workspace_edit.setText(path)

    def _save(self) -> None:
        values = {
            "approval_mode": self.approval_combo.currentData(),
            "shell_mode": self.shell_combo.currentData(),
            "max_steps": int(self.steps_spin.value()),
            "max_tokens": int(self.tokens_spin.value()),
            "temperature": float(self.temp_spin.value()),
            "model_override": self.model_edit.text().strip(),
            "workspace_dir": self.workspace_edit.text().strip(),
            "allow_network": bool(self.allow_network.isChecked()),
            "allow_private_network": bool(self.allow_private.isChecked()),
            "allow_self_modify": bool(self.allow_self.isChecked()),
            "auto_enable_plugin": bool(self.auto_enable.isChecked()),
            "notify_on_finish": bool(self.notify.isChecked()),
        }
        try:
            self.service.api.save_settings(values)
            self.service.reload_settings()
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        QMessageBox.information(self, "已保存", "设置已保存；下一轮任务生效。")
        self.accept()
