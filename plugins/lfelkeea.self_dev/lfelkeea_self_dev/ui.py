from __future__ import annotations

"""自开发工作台：任务输入、实时步骤、审批弹窗、设置。

界面只负责展示和收集决定，真正的执行在 service 的后台线程里。
"""

import html
import json
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, Signal
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
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import requirements as req_tools
from . import web as web_tools
from .connectors import ConnectorStore
from .envstore import EnvStore
from .knowledge import KnowledgeBase
from .resource_util import ResourceError
from .skills import SkillStore

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
QTabWidget::pane {{
    border: 1px solid {BORDER}; border-radius: 8px; background: {CARD};
    top: -1px;
}}
QTabBar::tab {{
    background: {CARD_2}; color: {MUTED}; border: 1px solid {BORDER};
    border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px;
    padding: 6px 16px; margin-right: 4px; font-family: "{FONT}"; font-size: 12px;
}}
QTabBar::tab:selected {{ color: {TEXT}; background: {CARD}; }}
QPlainTextEdit[readOnly="true"] {{ color: {TEXT}; background: #141013; }}
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
    (
        "先查资料再实现",
        "我想给桌宠加一个新插件，但不确定用哪个库。先看看技能库里有没有相关做法，"
        "再上网查官方文档和示例，确认方案后写代码；把关键结论存进知识库。",
    ),
    (
        "把流程沉淀成技能",
        "把我们刚才做插件时的发布流程整理成一个技能：写清适用场景、步骤、检查点和常见坑；"
        "正文控制在 4000 字以内，必要时拆到 references/ 资源里。",
    ),
]


class DevStudioDialog(QDialog):
    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self._files: list[str] = []
        self._last_step = 0
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
        self.resources_btn = QPushButton("技能 / 连接器 / 知识库")
        self.resources_btn.clicked.connect(self._open_resources)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.clicked.connect(self._open_settings)
        actions.addWidget(self.start_btn)
        actions.addWidget(self.stop_btn)
        actions.addStretch(1)
        actions.addWidget(self.resources_btn)
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

        side_layout.addWidget(self._section("维护"))
        self.restore_btn = QPushButton("一键还原到初始状态…")
        self.restore_btn.clicked.connect(self._restore_all)
        side_layout.addWidget(self.restore_btn)
        self.export_btn = QPushButton("导出插件…")
        self.export_btn.clicked.connect(self._export_plugin)
        side_layout.addWidget(self.export_btn)

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
        hint = QLabel("命令、连接器、删除和插件启用默认需要确认 · 所有改动自动备份")
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

    def _open_resources(self) -> None:
        dialog = ResourceDialog(self.service, self)
        dialog.exec()
        self._refresh_header()

    def _restore_all(self) -> None:
        if self.service.running:
            QMessageBox.information(self, "还在运行", "等这一轮结束再还原，避免改到一半。")
            return
        summary = self.service.change_summary()
        if summary.get("error"):
            QMessageBox.warning(self, "读不到改动记录", str(summary["error"]))
            return
        if not summary.get("count"):
            QMessageBox.information(
                self,
                "没有可还原的改动",
                "代理还没有在工作区、草稿或插件目录里改过东西。",
            )
            return
        listing = "\n".join(
            [f"文件：{item}" for item in summary["files"]]
            + [f"插件：{item}" for item in summary["plugins"]]
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("一键还原")
        box.setText(
            f"将还原 {len(summary['files'])} 个文件、{len(summary['plugins'])} 个插件。"
        )
        box.setInformativeText(
            "被改过的内容会回到代理第一次修改之前的状态；由代理新建的文件和插件会被移除。\n"
            "还原前会把当前状态备份到 .backups/restore/，所以还原本身还能再找回来。"
        )
        if listing:
            box.setDetailedText(listing)
        yes = box.addButton("还原", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        if box.clickedButton() is not yes:
            return
        result = self.service.restore_all()
        if result.get("error"):
            QMessageBox.warning(self, "还原失败", str(result["error"]))
            return
        removed = len(result.get("removed_files", [])) + len(result.get("removed_plugins", []))
        restored = len(result.get("restored_files", [])) + len(result.get("restored_plugins", []))
        errors = result.get("errors") or []
        message = (
            f"已还原 {restored} 项，移除新增的 {removed} 项。\n"
            f"还原前的状态备份在：\n{result.get('backup_dir', '')}"
        )
        if result.get("skipped_plugins"):
            message += "\n跳过（自开发工坊自身）：" + "、".join(result["skipped_plugins"])
        if errors:
            message += f"\n\n有 {len(errors)} 个问题，详情已写进执行日志。"
            QMessageBox.warning(self, "还原完成，但有提示", message)
        else:
            QMessageBox.information(self, "已还原", message)
        self._files.clear()
        self.files_list.clear()
        self._refresh_header()

    def _export_plugin(self) -> None:
        if self.service.running:
            QMessageBox.information(self, "还在运行", "等这一轮结束再导出，避免拿到写了一半的文件。")
            return
        plugins = self.service.installed_plugins()
        if not plugins:
            QMessageBox.information(self, "没有插件", "data/plugins 下还没有可导出的插件。")
            return
        labels = [
            f"{item['name']} · {item['id']} v{item['version']}" for item in plugins
        ]
        label, ok = QInputDialog.getItem(
            self, "导出插件", "选择要导出的插件：", labels, 0, False
        )
        if not ok or label not in labels:
            return
        chosen = plugins[labels.index(label)]
        default = str(
            Path.home() / f"{chosen['id']}-{chosen['version']}.zip"
        )
        path, ok = QFileDialog.getSaveFileName(
            self, "导出为 ZIP", default, "ZIP 压缩包 (*.zip)"
        )
        if not ok or not path.strip():
            return
        result = self.service.export_plugin(chosen["id"], path)
        if result.get("error"):
            QMessageBox.warning(self, "导出失败", str(result["error"]))
            return
        QMessageBox.information(
            self,
            "已导出",
            f"{result['name']} v{result['version']}\n"
            f"{result['files']} 个文件，ZIP 大小 {result['zip_bytes']} B\n\n"
            f"{result['path']}",
        )

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
            self.ai_label.setText(f"{info['provider']} / {info['model']} · 不限步数")
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
        if hasattr(self, "restore_btn"):
            self.restore_btn.setEnabled(not running)
            self.export_btn.setEnabled(not running)
        if not running:
            self.start_btn.setText("开始")
            self.status_label.setText("就绪")
            self.footer_label.setText(
                f"不限步数 · 输出长度由模型决定 · 工作区 {self.service.paths.workspace}"
            )

    def _tick(self) -> None:
        if not self.service.running:
            return
        elapsed = int(time.time() - self._started_at) if self._started_at else 0
        step = f"第 {self._last_step} 步 · " if self._last_step else ""
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
            self._append(
                f'<div style="color:{BORDER};margin:8px 0 2px 0;">'
                f'<span style="color:{MUTED};font-size:11px;">— 第 {self._last_step} 步 —</span></div>'
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
            self.restore_btn.setEnabled(True)
            self.export_btn.setEnabled(True)
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
            "自开发工坊直接使用桌宠「AI 与桌宠设置」里的接口和密钥；这里只覆盖模型名、温度和工具行为。"
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

        self.allow_network = QCheckBox("允许联网读取文档（fetch_url / 搜索）")
        self.allow_private = QCheckBox("允许访问本机和内网地址")
        self.allow_connectors = QCheckBox("允许使用连接器（HTTP / MCP）")
        self.allow_self = QCheckBox("允许修改自开发工坊自身（高风险）")
        self.auto_enable = QCheckBox("新插件通过检查后自动启用")
        self.auto_learn = QCheckBox("任务结束后自动把结论存入知识库")
        self.notify = QCheckBox("任务结束让桌宠说一句")
        form.addWidget(self.allow_network)
        form.addWidget(self.allow_private)
        form.addWidget(self.allow_connectors)
        form.addWidget(self.allow_self)
        form.addWidget(self.auto_enable)
        form.addWidget(self.auto_learn)
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
        self.temp_spin.setValue(float(settings["temperature"]))
        self.model_edit.setText(settings["model_override"])
        self.workspace_edit.setText(settings["workspace_dir"])
        self.allow_network.setChecked(bool(settings["allow_network"]))
        self.allow_private.setChecked(bool(settings["allow_private_network"]))
        self.allow_connectors.setChecked(bool(settings["allow_connectors"]))
        self.allow_self.setChecked(bool(settings["allow_self_modify"]))
        self.auto_enable.setChecked(bool(settings["auto_enable_plugin"]))
        self.auto_learn.setChecked(bool(settings["auto_learn"]))
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
            "temperature": float(self.temp_spin.value()),
            "model_override": self.model_edit.text().strip(),
            "workspace_dir": self.workspace_edit.text().strip(),
            "allow_network": bool(self.allow_network.isChecked()),
            "allow_private_network": bool(self.allow_private.isChecked()),
            "allow_connectors": bool(self.allow_connectors.isChecked()),
            "allow_self_modify": bool(self.allow_self.isChecked()),
            "auto_enable_plugin": bool(self.auto_enable.isChecked()),
            "auto_learn": bool(self.auto_learn.isChecked()),
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


# ---------------------------------------------------------------- 资源管理


class _Bridge(QObject):
    """把后台线程的结果送回界面线程。"""

    done = Signal(object)
    failed = Signal(str)


def _background(owner: Any, worker: Any, on_done: Any, on_failed: Any = None) -> None:
    bridge = _Bridge(owner)
    bridges = getattr(owner, "_bridges", None)
    if bridges is None:
        bridges = set()
        owner._bridges = bridges
    bridges.add(bridge)

    def finish(value: Any) -> None:
        bridges.discard(bridge)
        on_done(value)

    def fail(message: str) -> None:
        bridges.discard(bridge)
        if on_failed is not None:
            on_failed(message)
        else:
            QMessageBox.warning(owner, "操作失败", message)

    bridge.done.connect(finish)
    bridge.failed.connect(fail)

    def run() -> None:
        try:
            value = worker()
        except Exception as exc:  # 后台线程不能把异常抛给 Qt
            bridge.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            bridge.done.emit(value)

    threading.Thread(target=run, daemon=True).start()


def _block(title: str, body: str, meta: str = "") -> str:
    meta_html = (
        f'<div style="color:{MUTED};font-size:11px;margin:2px 0 10px 0;">{html.escape(meta)}</div>'
        if meta
        else ""
    )
    return (
        f'<div style="font-size:15px;font-weight:700;color:{TEXT};">{html.escape(title)}</div>'
        f"{meta_html}"
        f'<pre style="white-space:pre-wrap;font-family:{MONO};font-size:12px;color:{TEXT};">'
        f"{html.escape(body)}</pre>"
    )


class SkillEditorDialog(QDialog):
    def __init__(self, store: SkillStore, skill: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.skill = dict(skill or {})
        self.editing = bool(self.skill.get("id"))
        self.saved: dict[str, Any] | None = None
        self.setWindowTitle("编辑技能" if self.editing else "新建技能")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(840, 740)
        self.setStyleSheet(STYLE)
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("编辑技能" if self.editing else "新建技能")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "技能是给代理看的做法说明：写清楚什么时候用、步骤、检查点。"
            "正文超过 4 万字时，把细节拆到 references/ 资源里。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        panel = QFrame()
        panel.setObjectName("panel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("小写字母、数字、短横线，例如 weekly-report")
        self.id_edit.setEnabled(not self.editing)
        form.addWidget(self._row("技能 id", self.id_edit))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示名称，例如 周报生成")
        form.addWidget(self._row("名称", self.name_edit))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("一句话说明什么时候该用这个技能")
        form.addWidget(self._row("触发描述", self.desc_edit))
        self.enabled_check = QCheckBox("启用这个技能（关闭时不会进入系统提示，也不能读取）")
        self.enabled_check.setChecked(False)
        form.addWidget(self.enabled_check)
        self.req_env_edit = QLineEdit()
        self.req_env_edit.setPlaceholderText("需要主人配置的环境变量，例如 GITHUB_PAT, NOTION_TOKEN")
        form.addWidget(self._row("环境变量", self.req_env_edit))
        self.req_cmd_edit = QLineEdit()
        self.req_cmd_edit.setPlaceholderText("需要本机已有的命令，例如 npx, uvx, blender")
        form.addWidget(self._row("依赖命令", self.req_cmd_edit))
        self.body_edit = QPlainTextEdit()
        self.body_edit.setPlaceholderText(
            "# 适用场景\n\n# 步骤\n1. …\n\n# 检查点\n- …"
        )
        self.body_edit.setMinimumHeight(320)
        self.body_edit.setStyleSheet(f'font-family: "{MONO}";')
        form.addWidget(self.body_edit, 1)
        layout.addWidget(panel, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存技能")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _row(self, label: str, widget: Any) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        text = QLabel(label)
        text.setMinimumWidth(90)
        row.addWidget(text)
        row.addWidget(widget, 1)
        return holder

    def _load(self) -> None:
        if not self.skill:
            return
        self.id_edit.setText(str(self.skill.get("id", "")))
        self.name_edit.setText(str(self.skill.get("name", "")))
        self.desc_edit.setText(str(self.skill.get("description", "")))
        self.body_edit.setPlainText(str(self.skill.get("body", "")))
        self.enabled_check.setChecked(bool(self.skill.get("enabled", False)))
        self.req_env_edit.setText(", ".join(self.skill.get("requires_env") or []))
        self.req_cmd_edit.setText(", ".join(self.skill.get("requires_commands") or []))

    def _save(self) -> None:
        try:
            self.saved = self.store.save(
                self.id_edit.text().strip(),
                self.name_edit.text().strip(),
                self.desc_edit.text().strip(),
                self.body_edit.toPlainText(),
                enabled=self.enabled_check.isChecked(),
                requires_env=self.req_env_edit.text(),
                requires_commands=self.req_cmd_edit.text(),
            )
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()


class ConnectorEditorDialog(QDialog):
    def __init__(self, store: ConnectorStore, connector: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.connector = dict(connector or {})
        self.editing = bool(self.connector.get("id"))
        self.saved: dict[str, Any] | None = None
        self.setWindowTitle("编辑连接器" if self.editing else "新建连接器")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(820, 700)
        self.setStyleSheet(STYLE)
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("编辑连接器" if self.editing else "新建连接器")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "HTTP 连接器调用外部 API；MCP 连接器启动本地 stdio 服务器。"
            "密钥请写 ${ENV_NAME}，插件会在运行时从环境变量读取，不会存进 connectors.json。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        panel = QFrame()
        panel.setObjectName("panel")
        form = QVBoxLayout(panel)
        form.setContentsMargins(14, 12, 14, 12)
        form.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("例如 github")
        self.id_edit.setEnabled(not self.editing)
        head.addWidget(self._labelled("连接器 id", self.id_edit), 1)
        self.type_combo = QComboBox()
        self.type_combo.addItem("HTTP API", "http")
        self.type_combo.addItem("MCP 服务器", "mcp")
        self.type_combo.currentIndexChanged.connect(self._sync_type)
        head.addWidget(self._labelled("类型", self.type_combo), 1)
        form.addLayout(head)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("显示名称")
        name_row.addWidget(self._labelled("名称", self.name_edit), 1)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setDecimals(0)
        self.timeout_spin.setValue(45)
        self.timeout_spin.setSuffix(" 秒")
        name_row.addWidget(self._labelled("超时", self.timeout_spin))
        form.addLayout(name_row)

        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("给代理看的一句话说明：这个连接器能查什么")
        form.addWidget(self._labelled("说明", self.desc_edit))
        self.enabled_check = QCheckBox("启用这个连接器（关闭时不会进入系统提示，也不能调用）")
        self.enabled_check.setChecked(False)
        form.addWidget(self.enabled_check)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._http_page())
        self.stack.addWidget(self._mcp_page())
        form.addWidget(self.stack, 1)
        layout.addWidget(panel, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存连接器")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _labelled(self, label: str, widget: Any) -> QWidget:
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        text = QLabel(label)
        text.setMinimumWidth(72)
        row.addWidget(text)
        row.addWidget(widget, 1)
        return holder

    def _http_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.example.com/v1")
        form.addWidget(self._labelled("Base URL", self.base_url_edit))
        self.headers_edit = QPlainTextEdit()
        self.headers_edit.setPlaceholderText(
            '{\n  "Authorization": "Bearer ${MY_TOKEN}",\n  "Accept": "application/json"\n}'
        )
        self.headers_edit.setMinimumHeight(120)
        self.headers_edit.setStyleSheet(f'font-family: "{MONO}";')
        form.addWidget(QLabel("请求头（JSON，可用 ${ENV_NAME} 引用环境变量）"))
        form.addWidget(self.headers_edit, 1)
        self.allow_private = QCheckBox("允许访问本机和内网地址（仅在你信任时开启）")
        form.addWidget(self.allow_private)
        return page

    def _mcp_page(self) -> QWidget:
        page = QWidget()
        page.setStyleSheet("background: transparent;")
        form = QVBoxLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(8)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("例如 npx、python、D:/tools/server.exe")
        form.addWidget(self._labelled("命令", self.command_edit))
        self.args_edit = QPlainTextEdit()
        self.args_edit.setPlaceholderText("每行一个参数，例如：\n-y\n@modelcontextprotocol/server-filesystem")
        self.args_edit.setMinimumHeight(80)
        self.args_edit.setStyleSheet(f'font-family: "{MONO}";')
        form.addWidget(QLabel("参数（每行一个）"))
        form.addWidget(self.args_edit)
        self.env_edit = QPlainTextEdit()
        self.env_edit.setPlaceholderText('{\n  "API_KEY": "${MY_TOKEN}"\n}')
        self.env_edit.setMinimumHeight(80)
        self.env_edit.setStyleSheet(f'font-family: "{MONO}";')
        form.addWidget(QLabel("环境变量（JSON）"))
        form.addWidget(self.env_edit)
        self.cwd_edit = QLineEdit()
        self.cwd_edit.setPlaceholderText("可选：工作区内的相对路径")
        form.addWidget(self._labelled("工作目录", self.cwd_edit))
        return page

    def _sync_type(self) -> None:
        self.stack.setCurrentIndex(0 if self.type_combo.currentData() == "http" else 1)

    def _load(self) -> None:
        if not self.connector:
            self._sync_type()
            return
        kind = str(self.connector.get("type") or "http")
        self.id_edit.setText(str(self.connector.get("id", "")))
        self.name_edit.setText(str(self.connector.get("name", "")))
        self.desc_edit.setText(str(self.connector.get("description", "")))
        self.timeout_spin.setValue(float(self.connector.get("timeout") or 45))
        self.enabled_check.setChecked(bool(self.connector.get("enabled", False)))
        index = self.type_combo.findData(kind)
        self.type_combo.setCurrentIndex(max(0, index))
        if kind == "http":
            self.base_url_edit.setText(str(self.connector.get("base_url", "")))
            self.headers_edit.setPlainText(
                json.dumps(self.connector.get("headers") or {}, ensure_ascii=False, indent=2)
            )
            self.allow_private.setChecked(bool(self.connector.get("allow_private")))
        else:
            self.command_edit.setText(str(self.connector.get("command", "")))
            self.args_edit.setPlainText("\n".join(self.connector.get("args") or []))
            self.env_edit.setPlainText(
                json.dumps(self.connector.get("env") or {}, ensure_ascii=False, indent=2)
            )
            self.cwd_edit.setText(str(self.connector.get("cwd", "")))
        self._sync_type()

    def _json_object(self, widget: QPlainTextEdit, label: str) -> dict[str, Any]:
        text = widget.toPlainText().strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise ResourceError(f"{label}不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise ResourceError(f"{label}必须是 JSON 对象。")
        return value

    def _save(self) -> None:
        payload: dict[str, Any] = {
            "id": self.id_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "type": self.type_combo.currentData(),
            "description": self.desc_edit.text().strip(),
            "timeout": int(self.timeout_spin.value()),
            "enabled": bool(self.enabled_check.isChecked()),
        }
        try:
            if payload["type"] == "http":
                payload["base_url"] = self.base_url_edit.text().strip()
                payload["headers"] = self._json_object(self.headers_edit, "请求头")
                payload["allow_private"] = bool(self.allow_private.isChecked())
            else:
                payload["command"] = self.command_edit.text().strip()
                payload["args"] = [
                    line.strip()
                    for line in self.args_edit.toPlainText().splitlines()
                    if line.strip()
                ]
                payload["env"] = self._json_object(self.env_edit, "环境变量")
                payload["cwd"] = self.cwd_edit.text().strip()
            self.saved = self.store.save(payload)
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()


class TextImportDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title_text = ""
        self.body_text = ""
        self.setWindowTitle("导入文本到知识库")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(760, 600)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("导入文本")
        title.setObjectName("title")
        layout.addWidget(title)
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("文档标题，例如 桌宠插件踩坑记录")
        layout.addWidget(self.title_edit)
        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlaceholderText("粘贴要长期记住的内容……")
        layout.addWidget(self.text_edit, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("导入")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _save(self) -> None:
        body = self.text_edit.toPlainText().strip()
        if not body:
            QMessageBox.information(self, "还差内容", "先把要记住的文本粘贴进来。")
            return
        self.title_text = self.title_edit.text().strip() or body.splitlines()[0][:60]
        self.body_text = body
        self.accept()


class EnvVarsDialog(QDialog):
    """填写技能/连接器需要的环境变量；不回显已保存的值。"""

    def __init__(self, store: EnvStore, names: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.names = sorted({name for name in names if store.valid_name(name)})
        self.edits: dict[str, QLineEdit] = {}
        self.status_labels: dict[str, QLabel] = {}
        self.clear_buttons: dict[str, QPushButton] = {}
        self.secret_names = {
            name for name in self.names if req_tools.ENV_DOCS.get(name, {}).get("secret", True)
        }
        self.setWindowTitle("环境变量")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(640, 560)
        self.setStyleSheet(STYLE)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("环境变量")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "只保存在本机 data/dev/env.json，不进入模型上下文、会话日志或插件包。"
            "已保存的值不会回显；留空表示保持原值。系统环境变量已经配置的，也可以直接留空。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        if not self.names:
            empty = QLabel("这个条目没有声明需要环境变量。")
            empty.setObjectName("muted")
            layout.addWidget(empty)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        rows = QVBoxLayout(container)
        rows.setContentsMargins(0, 0, 6, 0)
        rows.setSpacing(12)
        for name in self.names:
            doc = req_tools.ENV_DOCS.get(name, {})
            label = str(doc.get("label") or name)
            header = QLabel(f"{label}  ·  {name}")
            header.setObjectName("section")
            header.setWordWrap(True)
            rows.addWidget(header)
            edit = QLineEdit()
            source = self.store.source(name)
            edit.setPlaceholderText(
                "输入新值覆盖当前设置；留空保持"
                if source != "missing"
                else "还没有配置，在这里填写"
            )
            if name in self.secret_names:
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.edits[name] = edit
            rows.addWidget(edit)
            line = QHBoxLayout()
            status = QLabel(self._status_text(name))
            status.setObjectName("ok" if source != "missing" else "warn")
            self.status_labels[name] = status
            line.addWidget(status, 1)
            if doc.get("help_url"):
                link = QLabel(f'<a href="{html.escape(str(doc["help_url"]))}">获取方式</a>')
                link.setOpenExternalLinks(True)
                link.setObjectName("muted")
                line.addWidget(link)
            clear = QPushButton("清除本地值")
            clear.setObjectName("ghost")
            clear.clicked.connect(lambda _checked=False, key=name: self._clear(key))
            self.clear_buttons[name] = clear
            clear.setVisible(source == "local")
            line.addWidget(clear)
            rows.addLayout(line)
            if doc.get("note"):
                hint = QLabel(str(doc["note"]))
                hint.setObjectName("muted")
                hint.setWordWrap(True)
                rows.addWidget(hint)
        rows.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        show = QCheckBox("显示输入内容")
        show.toggled.connect(self._toggle_echo)
        buttons.addWidget(show)
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    def _status_text(self, name: str) -> str:
        source = self.store.source(name)
        if source == "local":
            return "已保存到本机 env.json"
        if source == "system":
            return "已从系统环境变量读取"
        return "未配置"

    def _refresh_status(self, name: str) -> None:
        label = self.status_labels.get(name)
        if label is not None:
            source = self.store.source(name)
            label.setText(self._status_text(name))
            label.setObjectName("ok" if source != "missing" else "warn")
            label.style().unpolish(label)
            label.style().polish(label)
        button = self.clear_buttons.get(name)
        if button is not None:
            button.setVisible(self.store.source(name) == "local")

    def _clear(self, name: str) -> None:
        try:
            self.store.clear(name)
        except ResourceError as exc:
            QMessageBox.warning(self, "清除失败", str(exc))
            return
        edit = self.edits.get(name)
        if edit is not None:
            edit.clear()
        self._refresh_status(name)

    def _toggle_echo(self, visible: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        for name, edit in self.edits.items():
            if name in self.secret_names:
                edit.setEchoMode(mode)

    def _save(self) -> None:
        values = {
            name: edit.text().strip()
            for name, edit in self.edits.items()
            if edit.text().strip()
        }
        if values:
            try:
                self.store.set_many(values)
            except ResourceError as exc:
                QMessageBox.warning(self, "保存失败", str(exc))
                return
        self.accept()


class ResourceDialog(QDialog):
    """技能、连接器、知识库的统一管理界面。"""

    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.paths = service.paths
        self._bridges: set[_Bridge] = set()
        self._current_skills: dict[str, dict[str, Any]] = {}
        self._current_connectors: dict[str, dict[str, Any]] = {}
        self._current_documents: dict[int, dict[str, Any]] = {}
        self._requirement_statuses: dict[tuple[str, str], dict[str, Any]] = {}
        self._requirement_plan: dict[str, Any] = {}
        self._syncing = False
        existing_env = getattr(service, "env_store", None)
        env_file = getattr(self.paths, "env_file", None)
        if not env_file:
            base = getattr(self.paths, "dev_dir", None) or self.paths.workspace
            env_file = Path(base) / "env.json"
        self.env_store = (
            existing_env if isinstance(existing_env, EnvStore) else EnvStore(env_file).activate()
        )
        self.setWindowTitle("资源 · 技能 / 连接器 / 知识库 / 环境")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(1100, 780)
        self.setMinimumSize(920, 640)
        self.setStyleSheet(STYLE)
        self._build()
        self._refresh_skills()
        self._refresh_connectors()
        self._refresh_knowledge()
        self._refresh_requirements()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        title = QLabel("资源")
        title.setObjectName("title")
        layout.addWidget(title)
        note = QLabel(
            "技能教代理怎么做；连接器把外部 API / MCP 带进来；知识库保存需要反复查的资料。"
            "技能和连接器默认关闭：勾选左侧复选框即可启用，取消勾选即可关闭，不需要删除。"
            "启用后先到「环境与依赖」检查缺什么，再填写变量或下载资源。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._skill_tab(), "技能")
        self.tabs.addTab(self._connector_tab(), "连接器")
        self.tabs.addTab(self._knowledge_tab(), "知识库")
        self.tabs.addTab(self._requirements_tab(), "环境与依赖")
        layout.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.footer = QLabel("")
        self.footer.setObjectName("muted")
        footer.addWidget(self.footer, 1)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)

    def _skill_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.skill_search = QLineEdit()
        self.skill_search.setPlaceholderText("搜索技能名称、描述或正文")
        self.skill_search.returnPressed.connect(self._search_skills)
        bar.addWidget(self.skill_search, 1)
        find = QPushButton("搜索")
        find.clicked.connect(self._search_skills)
        bar.addWidget(find)
        refresh = QPushButton("刷新")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(lambda: self._refresh_skills())
        bar.addWidget(refresh)
        create = QPushButton("新建技能")
        create.setObjectName("primary")
        create.clicked.connect(self._new_skill)
        bar.addWidget(create)
        layout.addLayout(bar)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.skill_list = QListWidget()
        self.skill_list.currentItemChanged.connect(self._show_skill)
        self.skill_list.itemChanged.connect(self._toggle_skill)
        self.skill_list.itemDoubleClicked.connect(lambda _item: self._edit_skill())
        split.addWidget(self.skill_list)
        self.skill_detail = QTextBrowser()
        split.addWidget(self.skill_detail)
        split.setSizes([300, 720])
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.skill_count = QLabel("")
        self.skill_count.setObjectName("muted")
        bottom.addWidget(self.skill_count, 1)
        env_btn = QPushButton("环境 / 依赖")
        env_btn.clicked.connect(lambda: self._open_requirements("skill"))
        bottom.addWidget(env_btn)
        edit = QPushButton("编辑")
        edit.clicked.connect(self._edit_skill)
        bottom.addWidget(edit)
        delete = QPushButton("删除")
        delete.clicked.connect(self._delete_skill)
        bottom.addWidget(delete)
        layout.addLayout(bottom)
        return page

    def _connector_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.connector_search = QLineEdit()
        self.connector_search.setPlaceholderText("搜索连接器名称、id 或说明")
        self.connector_search.returnPressed.connect(self._search_connectors)
        bar.addWidget(self.connector_search, 1)
        find = QPushButton("搜索")
        find.clicked.connect(self._search_connectors)
        bar.addWidget(find)
        refresh = QPushButton("刷新")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(lambda: self._refresh_connectors())
        bar.addWidget(refresh)
        create = QPushButton("新建连接器")
        create.setObjectName("primary")
        create.clicked.connect(self._new_connector)
        bar.addWidget(create)
        layout.addLayout(bar)

        hint = QLabel("HTTP 连接器直接调用 API；MCP 连接器按需启动本地服务器进程。勾选复选框启用。")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.connector_list = QListWidget()
        self.connector_list.currentItemChanged.connect(self._show_connector)
        self.connector_list.itemChanged.connect(self._toggle_connector)
        self.connector_list.itemDoubleClicked.connect(lambda _item: self._edit_connector())
        split.addWidget(self.connector_list)
        self.connector_detail = QTextBrowser()
        split.addWidget(self.connector_detail)
        split.setSizes([300, 720])
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.connector_count = QLabel("")
        self.connector_count.setObjectName("muted")
        bottom.addWidget(self.connector_count, 1)
        env_btn = QPushButton("环境 / 依赖")
        env_btn.clicked.connect(lambda: self._open_requirements("connector"))
        bottom.addWidget(env_btn)
        test = QPushButton("测试")
        test.clicked.connect(self._test_connector)
        bottom.addWidget(test)
        edit = QPushButton("编辑")
        edit.clicked.connect(self._edit_connector)
        bottom.addWidget(edit)
        delete = QPushButton("删除")
        delete.clicked.connect(self._delete_connector)
        bottom.addWidget(delete)
        layout.addLayout(bottom)
        return page

    def _requirements_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.requirement_summary = QLabel("")
        self.requirement_summary.setObjectName("muted")
        bar.addWidget(self.requirement_summary, 1)
        self.req_show_disabled = QCheckBox("显示已关闭项")
        self.req_show_disabled.toggled.connect(lambda _checked: self._refresh_requirements())
        bar.addWidget(self.req_show_disabled)
        refresh = QPushButton("重新检查")
        refresh.setObjectName("primary")
        refresh.clicked.connect(self._refresh_requirements)
        bar.addWidget(refresh)
        layout.addLayout(bar)

        hint = QLabel(
            "检查已启用的技能和连接器：环境变量是否配置、MCP 启动命令是否存在、"
            "技能是否声明了可下载资源。这里只显示状态，不显示任何密钥值。"
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.requirement_list = QListWidget()
        self.requirement_list.currentItemChanged.connect(self._show_requirement)
        split.addWidget(self.requirement_list)
        self.requirement_detail = QTextBrowser()
        split.addWidget(self.requirement_detail)
        split.setSizes([320, 700])
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.requirement_hint = QLabel("")
        self.requirement_hint.setObjectName("muted")
        bottom.addWidget(self.requirement_hint, 1)
        self.env_button = QPushButton("填写环境变量")
        self.env_button.clicked.connect(self._edit_environment)
        bottom.addWidget(self.env_button)
        self.prepare_button = QPushButton("准备资源")
        self.prepare_button.clicked.connect(self._prepare_resources)
        bottom.addWidget(self.prepare_button)
        self.link_button = QPushButton("打开下载页")
        self.link_button.clicked.connect(self._open_requirement_link)
        bottom.addWidget(self.link_button)
        layout.addLayout(bottom)
        return page

    def _knowledge_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.knowledge_search = QLineEdit()
        self.knowledge_search.setPlaceholderText("搜索知识库，例如“插件权限”")
        self.knowledge_search.returnPressed.connect(self._search_knowledge)
        bar.addWidget(self.knowledge_search, 1)
        find = QPushButton("搜索")
        find.clicked.connect(self._search_knowledge)
        bar.addWidget(find)
        refresh = QPushButton("刷新")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(lambda: self._refresh_knowledge())
        bar.addWidget(refresh)
        layout.addLayout(bar)

        bar2 = QHBoxLayout()
        bar2.setSpacing(8)
        self.knowledge_stats = QLabel("")
        self.knowledge_stats.setObjectName("muted")
        bar2.addWidget(self.knowledge_stats, 1)
        text_btn = QPushButton("导入文本")
        text_btn.clicked.connect(self._import_text)
        bar2.addWidget(text_btn)
        file_btn = QPushButton("导入文件")
        file_btn.clicked.connect(self._import_file)
        bar2.addWidget(file_btn)
        url_btn = QPushButton("导入网址")
        url_btn.clicked.connect(self._import_url)
        bar2.addWidget(url_btn)
        layout.addLayout(bar2)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.knowledge_list = QListWidget()
        self.knowledge_list.currentItemChanged.connect(self._show_document)
        split.addWidget(self.knowledge_list)
        self.knowledge_detail = QTextBrowser()
        split.addWidget(self.knowledge_detail)
        split.setSizes([320, 700])
        split.setChildrenCollapsible(False)
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        hint = QLabel("命中片段会显示引用编号，代理在回答里可以据此回溯。")
        hint.setObjectName("muted")
        bottom.addWidget(hint, 1)
        delete = QPushButton("删除文档")
        delete.clicked.connect(self._delete_document)
        bottom.addWidget(delete)
        layout.addLayout(bottom)
        return page

    # ------------------------------------------------------------ 通用

    @property
    def skill_store(self) -> SkillStore:
        return SkillStore(self.paths.skills, self.paths.trash)

    @property
    def connector_store(self) -> ConnectorStore:
        return ConnectorStore(self.paths.connectors, self.paths.workspace, self.paths.trash)

    @property
    def knowledge_store(self) -> KnowledgeBase:
        return KnowledgeBase(self.paths.knowledge)

    def _selected_data(self, widget: QListWidget) -> Any:
        item = widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    # ------------------------------------------------------------ 技能

    def _refresh_skills(self, query: str = "") -> None:
        self._syncing = True
        try:
            self.skill_list.clear()
            self._current_skills.clear()
            try:
                store = self.skill_store
                items = (
                    store.search(query, limit=500, include_disabled=True)
                    if query
                    else store.list()
                )
            except (ResourceError, OSError) as exc:
                self.skill_detail.setHtml(
                    f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>'
                )
                return
            for item in items:
                label = f"{item['name']}\n{item['id']}"
                row = QListWidgetItem(label)
                row.setData(Qt.ItemDataRole.UserRole, item["id"])
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    Qt.CheckState.Checked
                    if item.get("enabled")
                    else Qt.CheckState.Unchecked
                )
                self.skill_list.addItem(row)
            self._update_skill_count(query)
            if items:
                self.skill_list.setCurrentRow(0)
            else:
                message = "没有匹配的技能。" if query else "还没有技能，点「新建技能」写一个。"
                self.skill_detail.setHtml(
                    f'<div style="color:{MUTED};">{html.escape(message)}</div>'
                )
        finally:
            self._syncing = False

    def _update_skill_count(self, query: str = "") -> None:
        total = self.skill_list.count()
        enabled = sum(
            1
            for index in range(total)
            if self.skill_list.item(index).checkState() == Qt.CheckState.Checked
        )
        text = f"共 {total} 个技能 · 已启用 {enabled} · 已关闭 {total - enabled}"
        if query:
            text += f" · 匹配「{query}」"
        self.skill_count.setText(text)

    def _toggle_skill(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        skill_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not skill_id:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            detail = self.skill_store.set_enabled(skill_id, enabled)
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "切换失败", str(exc))
            self._refresh_skills(self.skill_search.text().strip())
            return
        self._update_skill_count(self.skill_search.text().strip())
        self.footer.setText(
            f"技能 {skill_id}「{detail['name']}」{'已启用' if enabled else '已关闭'}"
        )
        if self.skill_list.currentItem() is item:
            self._show_skill(item)

    def _show_skill(self, current: Any, _previous: Any = None) -> None:
        if current is None:
            return
        skill_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        try:
            detail = self.skill_store.get(skill_id)
        except (ResourceError, OSError) as exc:
            self.skill_detail.setHtml(f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>')
            return
        self._current_skills[skill_id] = detail
        files = [name for name in detail.get("files", []) if name != "SKILL.md"]
        state = "已启用" if detail.get("enabled") else "已关闭（不会进入系统提示）"
        meta = (
            f"id: {detail['id']} · {state} · 更新于 {detail['updated']}"
            f" · 正文 {len(detail['body'])} 字"
        )
        if files:
            meta += "\n资源：" + "、".join(files)
        body = f"{detail['description']}\n\n{detail['body']}"
        self.skill_detail.setHtml(_block(detail["name"], body, meta))

    def _search_skills(self) -> None:
        self._refresh_skills(self.skill_search.text().strip())

    def _new_skill(self) -> None:
        dialog = SkillEditorDialog(self.skill_store, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            self._refresh_skills()
            self.footer.setText(f"已保存技能 {dialog.saved['id']}")

    def _edit_skill(self) -> None:
        skill_id = self._selected_data(self.skill_list)
        if not skill_id:
            QMessageBox.information(self, "先选一个技能", "在左侧选中要编辑的技能。")
            return
        try:
            detail = self.skill_store.get(str(skill_id))
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "读不到技能", str(exc))
            return
        dialog = SkillEditorDialog(self.skill_store, detail, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            self._refresh_skills()
            self.footer.setText(f"已保存技能 {dialog.saved['id']}")

    def _delete_skill(self) -> None:
        skill_id = self._selected_data(self.skill_list)
        if not skill_id:
            return
        if QMessageBox.question(
            self,
            "删除技能",
            f"删除技能 {skill_id}？\n会优先移到系统回收站，失败时放到 .trash/skills。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            message = self.skill_store.delete(str(skill_id))
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_skills()
        self.footer.setText(message)

    # ------------------------------------------------------------ 连接器

    def _refresh_connectors(self, query: str = "") -> None:
        self._syncing = True
        try:
            self.connector_list.clear()
            self._current_connectors.clear()
            try:
                items = self.connector_store.list()
            except (ResourceError, OSError) as exc:
                self.connector_detail.setHtml(
                    f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>'
                )
                return
            needle = query.strip().lower()
            if needle:
                items = [
                    item
                    for item in items
                    if needle
                    in " ".join(
                        str(item.get(key) or "")
                        for key in ("id", "name", "description", "type")
                    ).lower()
                ]
            for item in items:
                kind = "HTTP" if item.get("type") == "http" else "MCP"
                row = QListWidgetItem(f"{item.get('name') or item['id']}\n{kind} · {item['id']}")
                row.setData(Qt.ItemDataRole.UserRole, item["id"])
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(
                    Qt.CheckState.Checked
                    if item.get("enabled")
                    else Qt.CheckState.Unchecked
                )
                self.connector_list.addItem(row)
            self._update_connector_count(query)
            if items:
                self.connector_list.setCurrentRow(0)
            else:
                message = (
                    "没有匹配的连接器。" if query else "还没有连接器。可以接一个 HTTP API，或挂载 MCP 服务器。"
                )
                self.connector_detail.setHtml(
                    f'<div style="color:{MUTED};">{html.escape(message)}</div>'
                )
        finally:
            self._syncing = False

    def _update_connector_count(self, query: str = "") -> None:
        total = self.connector_list.count()
        enabled = sum(
            1
            for index in range(total)
            if self.connector_list.item(index).checkState() == Qt.CheckState.Checked
        )
        text = f"共 {total} 个连接器 · 已启用 {enabled} · 已关闭 {total - enabled}"
        if query:
            text += f" · 匹配「{query}」"
        self.connector_count.setText(text)

    def _toggle_connector(self, item: QListWidgetItem) -> None:
        if self._syncing:
            return
        connector_id = str(item.data(Qt.ItemDataRole.UserRole) or "")
        if not connector_id:
            return
        enabled = item.checkState() == Qt.CheckState.Checked
        try:
            detail = self.connector_store.set_enabled(connector_id, enabled)
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "切换失败", str(exc))
            self._refresh_connectors(self.connector_search.text().strip())
            return
        self._update_connector_count(self.connector_search.text().strip())
        self.footer.setText(
            f"连接器 {connector_id}「{detail['name']}」{'已启用' if enabled else '已关闭'}"
        )
        if self.connector_list.currentItem() is item:
            self._show_connector(item)

    def _search_connectors(self) -> None:
        self._refresh_connectors(self.connector_search.text().strip())

    def _show_connector(self, current: Any, _previous: Any = None) -> None:
        if current is None:
            return
        connector_id = str(current.data(Qt.ItemDataRole.UserRole) or "")
        try:
            detail = self.connector_store.get(connector_id)
        except (ResourceError, OSError) as exc:
            self.connector_detail.setHtml(f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>')
            return
        self._current_connectors[connector_id] = detail
        public = ConnectorStore.public(detail)
        state = "已启用" if detail.get("enabled") else "已关闭（不会进入系统提示，也不能调用）"
        meta = (
            f"{detail.get('type', '').upper()} · {state}"
            f" · 超时 {detail.get('timeout')} 秒"
        )
        body = json.dumps(public, ensure_ascii=False, indent=2)
        self.connector_detail.setHtml(
            _block(detail.get("name") or connector_id, body, meta + "\n密钥只显示引用或 ***")
        )

    def _new_connector(self) -> None:
        dialog = ConnectorEditorDialog(self.connector_store, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            self._refresh_connectors()
            self.footer.setText(f"已保存连接器 {dialog.saved['id']}")

    def _edit_connector(self) -> None:
        connector_id = self._selected_data(self.connector_list)
        if not connector_id:
            QMessageBox.information(self, "先选一个连接器", "在左侧选中要编辑的连接器。")
            return
        try:
            detail = self.connector_store.get(str(connector_id))
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "读不到连接器", str(exc))
            return
        dialog = ConnectorEditorDialog(self.connector_store, detail, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved:
            self._refresh_connectors()
            self.footer.setText(f"已保存连接器 {dialog.saved['id']}")

    def _delete_connector(self) -> None:
        connector_id = self._selected_data(self.connector_list)
        if not connector_id:
            return
        if QMessageBox.question(
            self,
            "删除连接器",
            f"删除连接器 {connector_id}？\n原 connectors.json 会保留一份 .bak 备份。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            message = self.connector_store.delete(str(connector_id))
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_connectors()
        self.footer.setText(message)

    def _test_connector(self) -> None:
        connector_id = self._selected_data(self.connector_list)
        if not connector_id:
            QMessageBox.information(self, "先选一个连接器", "在左侧选中要测试的连接器。")
            return
        store = self.connector_store
        self.footer.setText(f"正在测试 {connector_id}…")

        def done(message: Any) -> None:
            self.footer.setText("测试完成")
            QMessageBox.information(self, "连接器测试", str(message))

        def failed(message: str) -> None:
            self.footer.setText("测试失败")
            QMessageBox.warning(self, "连接器测试失败", message)

        _background(self, lambda: store.test(str(connector_id)), done, failed)

    # ------------------------------------------------------------ 环境与依赖

    def _selected_requirement_key(self) -> tuple[str, str] | None:
        item = self.requirement_list.currentItem()
        value = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return tuple(value) if isinstance(value, (tuple, list)) and len(value) == 2 else None

    def _open_requirements(self, kind: str) -> None:
        widget = self.connector_list if kind == "connector" else self.skill_list
        selected = self._selected_data(widget)
        if not selected:
            QMessageBox.information(self, "先选一个条目", "先在左侧选中要检查的技能或连接器。")
            return
        self.tabs.setCurrentIndex(3)
        self._refresh_requirements((kind, str(selected)))
        key = (kind, str(selected))
        for index in range(self.requirement_list.count()):
            item = self.requirement_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.requirement_list.setCurrentRow(index)
                break

    def _refresh_requirements(self, select: tuple[str, str] | None = None) -> None:
        current = select or self._selected_requirement_key()
        self._requirement_plan = {}
        self.requirement_list.blockSignals(True)
        try:
            self.requirement_list.clear()
            self._requirement_statuses.clear()
            try:
                statuses = req_tools.check_items(
                    self.skill_store, self.connector_store, self.env_store
                )
            except Exception as exc:
                self.requirement_summary.setText("检查失败")
                self.requirement_detail.setHtml(
                    f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>'
                )
                return
            enabled = [status for status in statuses if status["enabled"]]
            ready = sum(1 for status in enabled if status["ready"])
            missing = sum(1 for status in enabled if not status["ready"])
            self.requirement_summary.setText(
                f"已启用 {len(enabled)} 项 · 就绪 {ready} · 缺依赖 {missing}"
            )
            show_disabled = self.req_show_disabled.isChecked()
            shown = [
                status for status in statuses if status["enabled"] or show_disabled
            ]
            selected_row = None
            for status in shown:
                key = (status["kind"], status["id"])
                self._requirement_statuses[key] = status
                kind = "连接器" if status["kind"] == "connector" else "技能"
                state = req_tools.status_text(status)
                prefix = "" if status["enabled"] else "[关] "
                row = QListWidgetItem(f"{prefix}{status['name']}\n{kind} · {state}")
                row.setData(Qt.ItemDataRole.UserRole, key)
                self.requirement_list.addItem(row)
                if key == current:
                    selected_row = row
            if selected_row is not None:
                self.requirement_list.setCurrentItem(selected_row)
            elif self.requirement_list.count():
                self.requirement_list.setCurrentRow(0)
            else:
                message = (
                    "没有已启用的技能或连接器。先到「技能」或「连接器」页勾选要用的条目。"
                    if not show_disabled
                    else "还没有技能或连接器。"
                )
                self.requirement_detail.setHtml(
                    f'<div style="color:{MUTED};">{html.escape(message)}</div>'
                )
        finally:
            self.requirement_list.blockSignals(False)
        if self.requirement_list.currentItem() is not None:
            self._show_requirement(self.requirement_list.currentItem())

    def _fetch_requirement_item(self, kind: str, item_id: str) -> dict[str, Any] | None:
        try:
            return (
                self.connector_store.get(item_id)
                if kind == "connector"
                else self.skill_store.get(item_id)
            )
        except (ResourceError, OSError):
            return None

    def _show_requirement(self, current: Any, _previous: Any = None) -> None:
        if current is None:
            return
        raw_key = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_key, (tuple, list)) or len(raw_key) != 2:
            return
        kind, item_id = str(raw_key[0]), str(raw_key[1])
        status = self._requirement_statuses.get((kind, item_id))
        item = self._fetch_requirement_item(kind, item_id)
        if status is None or item is None:
            self.requirement_detail.setHtml(
                f'<div style="color:{MUTED};">这个条目已经不存在，点「重新检查」刷新。</div>'
            )
            return
        plan = req_tools.prepare_plan(kind, item, self.env_store)
        self._requirement_plan = plan
        env_names = [
            requirement["name"]
            for requirement in status["requirements"]
            if requirement["kind"] == "env"
        ]
        self.env_button.setEnabled(bool(env_names))
        self.prepare_button.setEnabled(bool(plan["has_actions"]))
        self.link_button.setEnabled(bool(plan["links"]))
        kind_label = "连接器" if kind == "connector" else "技能"
        state = req_tools.status_text(status)
        parts = [
            f'<div style="font-size:15px;font-weight:700;color:{TEXT};">'
            f"{html.escape(status['name'])}</div>",
            f'<div style="color:{MUTED};font-size:11px;margin:2px 0 10px 0;">'
            f"{kind_label} · {html.escape(item_id)} · {html.escape(state)}</div>",
        ]
        if status["requirements"]:
            parts.append(
                f'<div style="color:{MUTED};font-size:11px;letter-spacing:1px;'
                f'margin-top:8px;">依赖</div>'
            )
            for requirement in status["requirements"]:
                configured = bool(requirement["configured"])
                color = OK if configured else WARN
                if requirement["kind"] == "env":
                    label = "环境变量"
                    source = {
                        "local": "本机 env.json",
                        "system": "系统环境变量",
                        "missing": "未配置",
                    }.get(str(requirement.get("source")), "未配置")
                else:
                    label = "命令"
                    source = "已安装" if configured else "未找到"
                line = (
                    f'<div style="margin:4px 0;color:{TEXT};font-size:12px;">'
                    f'<span style="color:{color};">●</span> '
                    f"{label} <code>{html.escape(requirement['name'])}</code> · "
                    f"{html.escape(requirement['label'])} "
                    f'<span style="color:{MUTED};">（{source}）</span></div>'
                )
                if requirement.get("note"):
                    line += (
                        f'<div style="color:{MUTED};font-size:11px;margin:0 0 4px 16px;">'
                        f"{html.escape(str(requirement['note']))}</div>"
                    )
                parts.append(line)
        else:
            parts.append(
                f'<div style="color:{OK};margin-top:8px;">没有声明额外依赖。</div>'
            )
        if plan["downloads"]:
            parts.append(
                f'<div style="color:{MUTED};font-size:11px;letter-spacing:1px;'
                f'margin-top:10px;">可下载资源</div>'
            )
            for download in plan["downloads"]:
                parts.append(
                    f'<div style="margin:3px 0;color:{TEXT};font-size:12px;">'
                    f"{html.escape(download['label'])} → "
                    f"<code>{html.escape(download['path'])}</code></div>"
                )
        advice = list(plan["notes"]) + list(plan["blocked"])
        if advice:
            parts.append(
                f'<div style="color:{MUTED};font-size:11px;letter-spacing:1px;'
                f'margin-top:10px;">接下来</div>'
            )
            for line in advice:
                parts.append(
                    f'<div style="margin:3px 0;color:{WARN};font-size:11px;">'
                    f"{html.escape(str(line))}</div>"
                )
        if plan["links"]:
            parts.append(
                f'<div style="color:{MUTED};font-size:11px;letter-spacing:1px;'
                f'margin-top:10px;">手动下载</div>'
            )
            for link in plan["links"]:
                parts.append(
                    f'<div style="margin:3px 0;font-size:11px;">'
                    f'<a href="{html.escape(link["url"])}" style="color:{TOOL};">'
                    f"{html.escape(link['label'])}</a></div>"
                )
        self.requirement_detail.setHtml("".join(parts))
        self.requirement_hint.setText(
            "环境变量只在本机保存；填完点「重新检查」。" if env_names else ""
        )

    def _edit_environment(self) -> None:
        key = self._selected_requirement_key()
        if key is None:
            return
        status = self._requirement_statuses.get(key)
        if status is None:
            return
        names = [
            requirement["name"]
            for requirement in status["requirements"]
            if requirement["kind"] == "env"
        ]
        if not names:
            QMessageBox.information(self, "没有环境变量", "这个条目没有声明环境变量。")
            return
        dialog = EnvVarsDialog(self.env_store, names, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.footer.setText("环境变量已保存到本机")
            self._refresh_requirements(key)

    def _prepare_resources(self) -> None:
        key = self._selected_requirement_key()
        if key is None:
            return
        kind, item_id = key
        status = self._requirement_statuses.get(key)
        item = self._fetch_requirement_item(kind, item_id)
        if status is None or item is None:
            return
        plan = req_tools.prepare_plan(kind, item, self.env_store)
        lines = [f"准备 {status['name']}（{item_id}）："]
        for command in plan["commands"]:
            lines.append(f"运行：{' '.join(command['argv'])}")
        for download in plan["downloads"]:
            lines.append(f"下载：{download['url']} → {download['path']}")
        for note in plan["notes"] + plan["blocked"]:
            lines.append(f"提醒：{note}")
        if not plan["has_actions"]:
            for link in plan["links"]:
                lines.append(f"手动下载：{link['label']} {link['url']}")
            QMessageBox.information(self, "没有可自动准备的内容", "\n".join(lines))
            return
        if QMessageBox.question(
            self,
            "准备资源",
            "\n".join(lines)
            + "\n\n会联网下载并修改本机缓存或技能目录。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        self.prepare_button.setEnabled(False)
        self.footer.setText("正在准备资源…")
        workspace = self.paths.workspace
        timeout = max(120, min(1800, int(item.get("timeout", 600) or 600)))

        def worker() -> str:
            outputs: list[str] = []
            for command in plan["commands"]:
                outcome = req_tools.run_command(
                    command["argv"], cwd=workspace, timeout=timeout
                )
                state = "成功" if outcome["ok"] else f"失败（退出码 {outcome['code']}）"
                outputs.append(
                    f"$ {' '.join(command['argv'])}\n{state}\n{outcome['output']}"
                )
                if not outcome["ok"]:
                    outputs.append("后面的准备命令已跳过；先处理上面的错误。")
                    break
            else:
                if plan["downloads"]:
                    results = req_tools.download_skill_resources(
                        self.skill_store,
                        item_id,
                        plan["downloads"],
                        allow_private=bool(
                            getattr(self.service, "settings", {}).get(
                                "allow_private_network", False
                            )
                        ),
                    )
                    for result in results:
                        outputs.append(
                            f"下载 {result['path']}："
                            f"{'成功' if result['ok'] else '失败'}（{result.get('message', '')}）"
                        )
            return "\n\n".join(outputs) or "没有实际执行内容。"

        def done(text: Any) -> None:
            self.footer.setText("资源准备完成")
            self._show_output("资源准备结果", str(text))
            self._refresh_requirements(key)

        def failed(message: str) -> None:
            self.footer.setText("资源准备失败")
            QMessageBox.warning(self, "资源准备失败", message)

        _background(self, worker, done, failed)

    def _open_requirement_link(self) -> None:
        links = self._requirement_plan.get("links") or []
        if not links:
            QMessageBox.information(self, "没有下载页", "这个条目没有额外的下载页。")
            return
        target = links[0]
        if len(links) > 1:
            labels = [f"{item['label']} · {item['url']}" for item in links]
            chosen, ok = QInputDialog.getItem(self, "选择下载页", "打开哪个？", labels, 0, False)
            if not ok:
                return
            target = links[labels.index(chosen)]
        QDesktopServices.openUrl(QUrl(str(target["url"])))

    def _show_output(self, title: str, text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        dialog.resize(760, 520)
        dialog.setStyleSheet(STYLE)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 14)
        heading = QLabel(title)
        heading.setObjectName("title")
        layout.addWidget(heading)
        view = QTextBrowser()
        view.setPlainText(text)
        layout.addWidget(view, 1)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        dialog.exec()

    # ------------------------------------------------------------ 知识库

    def _refresh_knowledge(self, query: str = "") -> None:
        self.knowledge_list.clear()
        self._current_documents.clear()
        try:
            store = self.knowledge_store
            stats = store.stats()
        except Exception as exc:
            self.knowledge_detail.setHtml(f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>')
            return
        self.knowledge_stats.setText(
            f"{stats['documents']} 个文档 · {stats['chunks']} 个片段 · 约 {stats['chars']} 字"
        )
        try:
            if query:
                hits = store.search(query, limit=20)
                for hit in hits:
                    row = QListWidgetItem(f"#{hit['document_id']} {hit['title']}\n{hit['citation']}")
                    row.setData(Qt.ItemDataRole.UserRole, {"document_id": hit["document_id"], "hit": hit})
                    self.knowledge_list.addItem(row)
                label = f"「{query}」命中 {len(hits)} 个片段"
            else:
                documents = store.list_documents(limit=200)
                for doc in documents:
                    row = QListWidgetItem(
                        f"#{doc['id']} {doc['title']}\n{doc['kind']} · {doc['char_count']} 字 · {doc['created_at']}"
                    )
                    row.setData(Qt.ItemDataRole.UserRole, doc)
                    self.knowledge_list.addItem(row)
                label = f"共 {len(documents)} 个文档"
        except (ResourceError, OSError) as exc:
            self.knowledge_detail.setHtml(f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>')
            return
        self.knowledge_stats.setText(
            f"{label} · 共 {stats['documents']} 个文档 / {stats['chunks']} 个片段"
        )
        if self.knowledge_list.count():
            self.knowledge_list.setCurrentRow(0)
        else:
            self.knowledge_detail.setHtml(
                f'<div style="color:{MUTED};">'
                f'{"没有匹配的片段。" if query else "知识库还是空的，可以导入文本、文件或网址。"}'
                f"</div>"
            )

    def _show_document(self, current: Any, _previous: Any = None) -> None:
        if current is None:
            return
        data = current.data(Qt.ItemDataRole.UserRole) or {}
        if isinstance(data, dict):
            document_id = data.get("document_id") or data.get("id")
        else:
            document_id = data
        if document_id is None:
            return
        try:
            document = self.knowledge_store.get_document(int(document_id))
        except (ResourceError, OSError) as exc:
            self.knowledge_detail.setHtml(f'<div style="color:{ERROR};">{html.escape(str(exc))}</div>')
            return
        self._current_documents[int(document_id)] = document
        meta = (
            f"#{document['id']} · {document['kind']} · {document['char_count']} 字\n"
            f"来源：{document.get('source') or '（无）'}\n"
            f"创建：{document['created_at']} · 更新：{document['updated_at']}"
        )
        text = document["text"]
        if isinstance(data, dict) and data.get("hit"):
            hit = data["hit"]
            meta = f"{hit['citation']} · {meta}"
            text = f"命中片段：{hit['snippet']}\n\n────────\n\n{text}"
        if len(text) > 12000:
            text = text[:12000] + "\n…（预览已截断，代理检索时仍能看到完整片段）"
        self.knowledge_detail.setHtml(_block(document["title"], text, meta))

    def _search_knowledge(self) -> None:
        self._refresh_knowledge(self.knowledge_search.text().strip())

    def _add_document(self, title: str, text: str, source: str) -> None:
        try:
            result = self.knowledge_store.add_document(title, text, source=source, kind="note")
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._refresh_knowledge()
        if result.get("existing"):
            self.footer.setText(f"内容已存在，未重复导入（#{result['id']}）。")
            QMessageBox.information(self, "已存在", f"同样的内容已经在知识库里（#{result['id']}），没有重复导入。")
        else:
            self.footer.setText(f"已导入文档 #{result['id']}，{result['chunks']} 个片段。")

    def _import_text(self) -> None:
        dialog = TextImportDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._add_document(dialog.title_text, dialog.body_text, "manual")

    def _import_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择要导入知识库的文件",
            str(self.paths.workspace),
            "文本文件 (*.md *.txt *.py *.json *.log *.csv *.html *.htm *.xml *.yaml *.yml);;所有文件 (*.*)",
        )
        if not path:
            return
        target = Path(path)

        def worker() -> dict[str, Any]:
            return self.knowledge_store.import_file(target, title=target.stem, source=str(target))

        def done(result: dict[str, Any]) -> None:
            self._refresh_knowledge()
            if result.get("existing"):
                QMessageBox.information(self, "已存在", f"内容已存在（#{result['id']}），没有重复导入。")
            else:
                QMessageBox.information(
                    self, "已导入", f"{result['title']}\n{result['chunks']} 个片段。"
                )

        self.footer.setText(f"正在导入 {target.name}…")
        _background(self, worker, done)

    def _import_url(self) -> None:
        if not self.service.settings.get("allow_network", True):
            QMessageBox.warning(self, "联网已关闭", "先在设置里允许联网读取文档。")
            return
        url, ok = QInputDialog.getText(
            self, "导入网址", "网页或文本接口地址（http/https）：", QLineEdit.EchoMode.Normal, ""
        )
        url = str(url or "").strip()
        if not ok or not url:
            return

        def worker() -> dict[str, Any]:
            title, body = web_tools.fetch_readable(url, max_chars=200_000)
            return self.knowledge_store.add_document(
                title or url, body, source=url, kind="web"
            )

        def done(result: dict[str, Any]) -> None:
            self._refresh_knowledge()
            if result.get("existing"):
                QMessageBox.information(self, "已存在", f"内容已存在（#{result['id']}），没有重复导入。")
            else:
                QMessageBox.information(
                    self, "已导入", f"{result['title']}\n{result['chunks']} 个片段。"
                )

        def failed(message: str) -> None:
            self.footer.setText("导入失败")
            QMessageBox.warning(self, "导入失败", message)

        self.footer.setText(f"正在抓取 {url}…")
        _background(self, worker, done, failed)

    def _delete_document(self) -> None:
        current = self.knowledge_list.currentItem()
        if current is None:
            return
        data = current.data(Qt.ItemDataRole.UserRole) or {}
        if isinstance(data, dict):
            document_id = data.get("document_id") or data.get("id")
        else:
            document_id = data
        if document_id is None:
            return
        if QMessageBox.question(
            self,
            "删除知识文档",
            f"删除文档 #{document_id}？\n删除前会在 .trash/knowledge 留一份 Markdown 备份。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            title, backup = self.knowledge_store.archive_and_delete(int(document_id), self.paths.trash)
        except (ResourceError, OSError) as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self._refresh_knowledge()
        self.footer.setText(f"已删除《{title}》，备份在 {backup.name}。")
