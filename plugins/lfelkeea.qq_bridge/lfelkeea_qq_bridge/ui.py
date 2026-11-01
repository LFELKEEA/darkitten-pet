from __future__ import annotations

"""QQ 桥接的设置窗口：连接、白名单、人格、记忆、黑话、习惯、空间和诊断。"""

import re
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QPoint, QTimer, QUrl
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
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
FONT = "Microsoft YaHei UI"

STYLE = f"""
QDialog {{
    background: transparent;
}}
QFrame#card {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QLabel {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
    background: transparent;
}}
QLabel#title {{
    font-size: 17px;
    font-weight: 600;
}}
QLabel#hint {{
    color: {MUTED};
    font-size: 11px;
}}
QLabel#state {{
    font-size: 12px;
    font-weight: 600;
}}
QLabel#section {{
    color: {MUTED};
    font-size: 11px;
    letter-spacing: 1px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-radius: 10px;
    background-color: #1a1316;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 14px;
    margin-right: 4px;
    border-radius: 8px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
}}
QTabBar::tab:selected {{
    background-color: {CARD_2};
    color: {TEXT};
}}
QTabBar::tab:hover {{
    color: {TEXT};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit {{
    background-color: #120d0f;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 9px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus {{
    border: 1px solid {ACCENT_LIGHT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: #1a1316;
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    outline: none;
}}
QCheckBox {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: #120d0f;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT_LIGHT};
}}
QPushButton {{
    background-color: {CARD_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 14px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: #38272d;
}}
QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT_LIGHT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_LIGHT};
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
    padding: 6px 9px;
}}
QPushButton#ghost:hover {{
    color: {TEXT};
    background: {CARD_2};
}}
QGroupBox {{
    color: {MUTED};
    border: 1px solid {BORDER};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 12px 10px 12px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 11px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {MUTED};
}}
QListWidget, QTableWidget {{
    background-color: #120d0f;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 12px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 8px;
}}
QListWidget::item:selected, QTableWidget::item:selected {{
    background-color: {ACCENT};
    color: #ffffff;
}}
QTableWidget::item {{
    padding: 4px 6px;
}}
QHeaderView::section {{
    background-color: {CARD_2};
    color: {MUTED};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
    font-size: 11px;
}}
QTableCornerButton::section {{
    background-color: {CARD_2};
    border: none;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #4a333a;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #63434b;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def _line() -> QFrame:
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background-color: {BORDER}; border: none;")
    return line


class _DragHeader(QWidget):
    """无边框窗口的拖动区域。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._drag_origin: QPoint | None = None
        self._window_origin: QPoint | None = None
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._window_origin = self.window().pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_origin is not None and self._window_origin is not None:
            delta = event.globalPosition().toPoint() - self._drag_origin
            self.window().move(self._window_origin + delta)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_origin = None
        self._window_origin = None
        super().mouseReleaseEvent(event)


class QQBridgeDialog(QDialog):
    def __init__(self, service: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("QQ 桥接 · 设置")
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(600, 420)

        self._build()
        self._load_settings()
        self._refresh_all()

        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()

    # ---------- 位置与尺寸 ----------

    def _place(self) -> None:
        """按当前屏幕可用区域决定大小：不超过屏幕，并整体落在屏幕内。"""
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        width = min(900, max(600, area.width() - 80))
        height = min(760, max(420, area.height() - 80))
        self.resize(width, height)

        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            center = parent.frameGeometry().center()
        else:
            center = area.center()
        x = center.x() - width // 2
        y = center.y() - height // 2
        x = max(area.left(), min(x, area.right() - width + 1))
        y = max(area.top(), min(y, area.bottom() - height + 1))
        self.move(x, y)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_placed", False):
            self._placed = True
            self._place()

    # ---------- 布局 ----------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header = _DragHeader(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("QQ 桥接")
        title.setObjectName("title")
        subtitle = _hint("通过 SnowLuma 连接 QQ。改完点“保存并应用”，白名单和人格等会立即生效。")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        self.state_label = QLabel("")
        self.state_label.setObjectName("state")
        close_btn = QPushButton("×")
        close_btn.setObjectName("ghost")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ font-size: 16px; color: {MUTED}; background: transparent; border: none; }}"
            f"QPushButton:hover {{ color: #ffffff; background: {ACCENT}; border-radius: 6px; }}"
        )
        close_btn.clicked.connect(self.accept)
        header_layout.addLayout(title_box, 1)
        header_layout.addWidget(self.state_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header_layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(header)
        layout.addWidget(_line())

        self.tabs = QTabWidget()
        for builder, label in (
            (self._build_connection, "连接"),
            (self._build_whitelist, "白名单"),
            (self._build_persona, "人格"),
            (self._build_memory, "记忆"),
            (self._build_slang, "黑话"),
            (self._build_habits, "习惯"),
            (self._build_qzone, "QQ 空间"),
            (self._build_voice, "语音"),
            (self._build_advanced, "高级"),
            (self._build_diagnostics, "诊断"),
        ):
            self.tabs.addTab(self._scroll_page(builder()), label)
        layout.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.message = QLabel("")
        self.message.setObjectName("hint")
        self.message.setWordWrap(True)
        folder_btn = QPushButton("打开数据目录")
        folder_btn.setObjectName("ghost")
        folder_btn.clicked.connect(self._open_folder)
        apply_btn = QPushButton("保存并应用")
        apply_btn.setObjectName("primary")
        apply_btn.clicked.connect(self._apply_settings)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.message, 1)
        bottom.addWidget(folder_btn)
        bottom.addWidget(apply_btn)
        bottom.addWidget(close_btn)
        grip = QSizeGrip(card)
        grip.setFixedSize(14, 14)
        bottom.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(bottom)

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        return page, layout

    def _scroll_page(self, page: QWidget) -> QWidget:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(page)
        return area

    # ---------- 连接 ----------

    def _build_connection(self) -> QWidget:
        page, layout = self._page()

        form_box = QGroupBox("SnowLuma")
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        self.http_edit = QLineEdit()
        self.http_edit.setPlaceholderText("http://127.0.0.1:3000")
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("SnowLuma HTTP 适配器的 accessToken，没有就留空")
        self.owner_edit = QLineEdit()
        self.owner_edit.setPlaceholderText("你的 QQ 号，用指令管理和接收提醒")
        form.addRow("HTTP 地址", self.http_edit)
        form.addRow("Access Token", self.token_edit)
        form.addRow("主人 QQ", self.owner_edit)
        layout.addWidget(form_box)

        auto_box = QGroupBox("SnowLuma 自动启动")
        auto_form = QFormLayout(auto_box)
        auto_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        auto_form.setHorizontalSpacing(12)
        auto_form.setVerticalSpacing(8)
        self.auto_start_snowluma = QCheckBox("插件启用时在后台启动 SnowLuma")
        self.snowluma_dir_edit = QLineEdit()
        self.snowluma_dir_edit.setPlaceholderText(r"例如 D:\SnowLuma，留空则自动查找")
        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        dir_row.addWidget(self.snowluma_dir_edit, 1)
        detect_btn = QPushButton("自动检测")
        detect_btn.setObjectName("ghost")
        detect_btn.clicked.connect(self._detect_snowluma_dir)
        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("ghost")
        browse_btn.clicked.connect(self._browse_snowluma_dir)
        dir_row.addWidget(detect_btn)
        dir_row.addWidget(browse_btn)
        self.auto_state_label = QLabel("")
        self.auto_state_label.setObjectName("hint")
        self.auto_state_label.setWordWrap(True)
        takeover_btn = QPushButton("接管并重启")
        takeover_btn.setObjectName("ghost")
        takeover_btn.setToolTip("结束正在运行的手动 SnowLuma（node.exe），再由插件在后台启动")
        takeover_btn.clicked.connect(self._takeover_snowluma)
        takeover_row = QHBoxLayout()
        takeover_row.addWidget(takeover_btn)
        takeover_row.addStretch(1)
        auto_form.addRow("", self.auto_start_snowluma)
        auto_form.addRow("程序目录", dir_row)
        auto_form.addRow("", takeover_row)
        auto_form.addRow("", self.auto_state_label)
        layout.addWidget(auto_box)
        layout.addWidget(
            _hint(
                "开启后，插件加载时如果 SnowLuma 没在运行，会在后台启动它，不弹控制台窗口。"
                "关闭插件或退出桌宠时，由插件启动的 SnowLuma 会被一起关闭；"
                "你自己手动启动的实例不会被接管。"
            )
        )

        event_box = QGroupBox("本机事件接收")
        event_form = QFormLayout(event_box)
        event_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        event_form.setHorizontalSpacing(12)
        event_form.setVerticalSpacing(8)
        self.host_edit = QLineEdit()
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("/onebot")
        self.secret_edit = QLineEdit()
        self.secret_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret_edit.setPlaceholderText("与 SnowLuma 事件上报的 accessToken 保持一致，可留空")
        event_form.addRow("监听地址", self.host_edit)
        event_form.addRow("监听端口", self.port_spin)
        event_form.addRow("事件路径", self.path_edit)
        event_form.addRow("事件密钥", self.secret_edit)
        layout.addWidget(event_box)

        self.event_url_label = QLabel("")
        self.event_url_label.setObjectName("hint")
        self.event_url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.event_url_label)

        actions = QHBoxLayout()
        test_btn = QPushButton("检测连接")
        test_btn.clicked.connect(self._probe)
        copy_btn = QPushButton("复制事件地址")
        copy_btn.setObjectName("ghost")
        copy_btn.clicked.connect(self._copy_event_url)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        actions.addWidget(test_btn)
        actions.addWidget(copy_btn)
        actions.addWidget(self.test_result, 1)
        layout.addLayout(actions)

        layout.addWidget(
            _hint(
                "SnowLuma 里要启用 HTTP 服务，并把事件上报地址填成上面这个地址；"
                "如果 SnowLuma 配了 accessToken，这里的事件密钥也填同一个值。"
            )
        )
        layout.addStretch(1)
        return page

    # ---------- 白名单 ----------

    def _build_whitelist(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            _hint(
                "只有白名单里的私聊和群会被响应，也才会收到主动消息。主人 QQ 自动放行。\n"
                "备注只用于自己辨认，例如“室友小明”，不影响白名单匹配。"
            )
        )
        self.owner_label = QLabel("")
        self.owner_label.setObjectName("hint")
        layout.addWidget(self.owner_label)

        private_box = QGroupBox("好友 / 私聊")
        private_layout = QVBoxLayout(private_box)
        private_layout.setSpacing(8)
        self.private_list = QListWidget()
        self.private_list.setMinimumHeight(130)
        self.private_list.itemSelectionChanged.connect(
            lambda: self._load_allow_note("private")
        )
        self.private_input = QLineEdit()
        self.private_input.setPlaceholderText("QQ 号，纯数字")
        self.private_note = QLineEdit()
        self.private_note.setPlaceholderText("备注（可选，最多 40 字），例如：室友小明")
        self.private_note.setMaxLength(40)
        private_actions = QHBoxLayout()
        add_private = QPushButton("添加")
        add_private.setObjectName("primary")
        add_private.clicked.connect(lambda: self._allow_change("private", True))
        del_private = QPushButton("删除选中")
        del_private.clicked.connect(lambda: self._allow_change("private", False))
        private_actions.addWidget(self.private_input, 1)
        private_actions.addWidget(add_private)
        private_actions.addWidget(del_private)
        private_note_row = QHBoxLayout()
        save_private_note = QPushButton("保存备注")
        save_private_note.clicked.connect(lambda: self._save_allow_note("private"))
        private_note_row.addWidget(self.private_note, 1)
        private_note_row.addWidget(save_private_note)
        private_layout.addWidget(self.private_list)
        private_layout.addLayout(private_actions)
        private_layout.addLayout(private_note_row)
        layout.addWidget(private_box)

        group_box = QGroupBox("群聊")
        group_layout = QVBoxLayout(group_box)
        group_layout.setSpacing(8)
        self.group_list = QListWidget()
        self.group_list.setMinimumHeight(130)
        self.group_list.itemSelectionChanged.connect(
            lambda: self._load_allow_note("group")
        )
        self.group_input = QLineEdit()
        self.group_input.setPlaceholderText("群号，纯数字")
        self.group_note = QLineEdit()
        self.group_note.setPlaceholderText("备注（可选，最多 40 字），例如：公司摸鱼群")
        self.group_note.setMaxLength(40)
        group_actions = QHBoxLayout()
        add_group = QPushButton("添加")
        add_group.setObjectName("primary")
        add_group.clicked.connect(lambda: self._allow_change("group", True))
        del_group = QPushButton("删除选中")
        del_group.clicked.connect(lambda: self._allow_change("group", False))
        group_actions.addWidget(self.group_input, 1)
        group_actions.addWidget(add_group)
        group_actions.addWidget(del_group)
        group_note_row = QHBoxLayout()
        save_group_note = QPushButton("保存备注")
        save_group_note.clicked.connect(lambda: self._save_allow_note("group"))
        group_note_row.addWidget(self.group_note, 1)
        group_note_row.addWidget(save_group_note)
        group_layout.addWidget(self.group_list)
        group_layout.addLayout(group_actions)
        group_layout.addLayout(group_note_row)
        layout.addWidget(group_box)

        watch_box = QGroupBox("盯人")
        watch_layout = QVBoxLayout(watch_box)
        watch_layout.setSpacing(8)
        watch_layout.addWidget(
            _hint(
                "盯人名单里的 QQ 私聊时不用 @ 或叫名字，也不受接话概率限制，会直接回复；"
                "在群里只有白名单群会响应。盯人名单本身也可以直接私聊。"
            )
        )
        self.watch_list = QListWidget()
        self.watch_list.setMinimumHeight(100)
        self.watch_input = QLineEdit()
        self.watch_input.setPlaceholderText("要盯的 QQ 号，纯数字")
        watch_actions = QHBoxLayout()
        add_watch = QPushButton("开始盯")
        add_watch.setObjectName("primary")
        add_watch.clicked.connect(self._watch_add)
        del_watch = QPushButton("取消盯")
        del_watch.clicked.connect(self._watch_remove)
        watch_actions.addWidget(self.watch_input, 1)
        watch_actions.addWidget(add_watch)
        watch_actions.addWidget(del_watch)
        watch_layout.addWidget(self.watch_list)
        watch_layout.addLayout(watch_actions)
        layout.addWidget(watch_box)
        return page

    # ---------- 人格 ----------

    def _build_persona(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            _hint("人格卡会作为系统提示的一部分发给 AI。写清楚角色、语气和禁忌即可，最多 8000 字。")
        )
        self.persona_edit = QPlainTextEdit()
        self.persona_edit.setPlaceholderText("例如：你是一只桌面小猫，说话简短自然，不用列表。")
        layout.addWidget(self.persona_edit, 1)
        row = QHBoxLayout()
        self.persona_count = QLabel("")
        self.persona_count.setObjectName("hint")
        save_btn = QPushButton("保存人格卡")
        save_btn.setObjectName("primary")
        save_btn.clicked.connect(self._save_persona)
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_persona)
        row.addWidget(self.persona_count, 1)
        row.addWidget(reset_btn)
        row.addWidget(save_btn)
        layout.addLayout(row)
        self.persona_edit.textChanged.connect(self._update_persona_count)
        return page

    # ---------- 记忆 ----------

    def _build_memory(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            _hint("长期记忆会随聊天自动积累。可以点“立即整理”手动总结，也可以补充或删除内容。")
        )
        status_row = QHBoxLayout()
        self.memory_status = QLabel("")
        self.memory_status.setObjectName("hint")
        compact_btn = QPushButton("立即整理")
        compact_btn.clicked.connect(self._compact_memory)
        status_row.addWidget(self.memory_status, 1)
        status_row.addWidget(compact_btn)
        layout.addLayout(status_row)
        self.memory_list = QListWidget()
        layout.addWidget(self.memory_list, 1)
        row = QHBoxLayout()
        self.memory_input = QLineEdit()
        self.memory_input.setPlaceholderText("要记住的事，例如：主人喜欢喝冰美式")
        add_btn = QPushButton("添加")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_memory)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_memory)
        row.addWidget(self.memory_input, 1)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        layout.addLayout(row)
        return page

    # ---------- 黑话 ----------

    def _build_slang(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            _hint("黑话库记录群里的特殊说法。聊天时只把相关词条带给 AI，不会把所有词条都塞进去。")
        )
        self.slang_table = QTableWidget(0, 3)
        self.slang_table.setHorizontalHeaderLabels(["词", "含义", "出现次数"])
        slang_header = self.slang_table.horizontalHeader()
        slang_header.setStretchLastSection(False)
        slang_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        slang_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        slang_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.slang_table.verticalHeader().setVisible(False)
        self.slang_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.slang_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.slang_table, 1)
        row = QHBoxLayout()
        self.slang_term = QLineEdit()
        self.slang_term.setPlaceholderText("词")
        self.slang_meaning = QLineEdit()
        self.slang_meaning.setPlaceholderText("含义（可留空）")
        add_btn = QPushButton("添加")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_slang)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_slang)
        row.addWidget(self.slang_term, 1)
        row.addWidget(self.slang_meaning, 2)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        layout.addLayout(row)
        return page

    # ---------- 习惯 ----------

    def _build_habits(self) -> QWidget:
        page, layout = self._page()
        layout.addWidget(
            _hint("这里统计各个会话的消息习惯。插件会根据这些数据调整回复长度和表情概率，不需要的会话可以删掉。")
        )
        self.habit_table = QTableWidget(0, 6)
        self.habit_table.setHorizontalHeaderLabels(
            ["会话", "消息数", "短句率", "问句", "表情", "最后活跃"]
        )
        header = self.habit_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.habit_table.setColumnWidth(0, 240)
        for index in range(1, 5):
            header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.habit_table.verticalHeader().setVisible(False)
        self.habit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.habit_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.habit_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.habit_table, 1)
        row = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self._refresh_habits)
        del_btn = QPushButton("删除选中")
        del_btn.clicked.connect(self._delete_habit)
        row.addStretch(1)
        row.addWidget(del_btn)
        row.addWidget(refresh_btn)
        layout.addLayout(row)
        return page

    # ---------- QQ 空间 ----------

    def _build_qzone(self) -> QWidget:
        page, layout = self._page()
        self.qzone_enabled = QCheckBox("允许发 QQ 空间")
        layout.addWidget(self.qzone_enabled)

        form_box = QGroupBox("可见范围")
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        self.qzone_perm = QComboBox()
        for label, key in (
            ("公开", "public"),
            ("好友可见", "friends"),
            ("部分好友", "some"),
            ("仅自己", "private"),
            ("隐藏", "hide"),
        ):
            self.qzone_perm.addItem(label, key)
        self.qzone_targets = QLineEdit()
        self.qzone_targets.setPlaceholderText("部分好友可见时填 QQ 号，逗号分隔")
        form.addRow("权限", self.qzone_perm)
        form.addRow("指定好友", self.qzone_targets)
        layout.addWidget(form_box)

        publish_box = QGroupBox("发一条说说")
        publish_layout = QVBoxLayout(publish_box)
        self.qzone_text = QPlainTextEdit()
        self.qzone_text.setPlaceholderText("说点什么…")
        self.qzone_text.setFixedHeight(90)
        publish_row = QHBoxLayout()
        publish_btn = QPushButton("发布")
        publish_btn.setObjectName("primary")
        publish_btn.clicked.connect(self._publish_qzone)
        publish_row.addStretch(1)
        publish_row.addWidget(publish_btn)
        publish_layout.addWidget(self.qzone_text)
        publish_layout.addLayout(publish_row)
        layout.addWidget(publish_box)

        auto_box = QGroupBox("自动回复评论")
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.setSpacing(8)
        self.qzone_auto_reply = QCheckBox("自动回复我最近说说下的新评论")
        self.qzone_reply_interval = QSpinBox()
        self.qzone_reply_interval.setRange(1, 1440)
        self.qzone_reply_interval.setSuffix(" 分钟")
        self.qzone_reply_max = QSpinBox()
        self.qzone_reply_max.setRange(1, 10)
        self.qzone_reply_max.setSuffix(" 条 / 轮")
        auto_form = QFormLayout()
        auto_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        auto_form.setHorizontalSpacing(12)
        auto_form.addRow("检查间隔", self.qzone_reply_interval)
        auto_form.addRow("每轮最多", self.qzone_reply_max)
        auto_layout.addWidget(self.qzone_auto_reply)
        auto_layout.addLayout(auto_form)
        self.qzone_auto_status = _hint("")
        auto_layout.addWidget(self.qzone_auto_status)
        auto_layout.addWidget(
            _hint(
                "只读取自己最近 5 条说说，遇到没回复过的新评论时用 AI 写一句回复。"
                "第一次开启只记录已有评论，不会补回复旧评论；关闭后立即停止。"
            )
        )
        layout.addWidget(auto_box)

        manage_box = QGroupBox("最近说说")
        manage_layout = QVBoxLayout(manage_box)
        self.qzone_result = QPlainTextEdit()
        self.qzone_result.setReadOnly(True)
        self.qzone_result.setFixedHeight(110)
        row = QHBoxLayout()
        list_btn = QPushButton("读取最近说说")
        list_btn.clicked.connect(lambda: self._qzone_action("list"))
        self.qzone_tid = QLineEdit()
        self.qzone_tid.setPlaceholderText("说说 id（删除时填）")
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(self._delete_qzone)
        row.addWidget(list_btn)
        row.addWidget(self.qzone_tid, 1)
        row.addWidget(del_btn)
        manage_layout.addWidget(self.qzone_result)
        manage_layout.addLayout(row)
        layout.addWidget(manage_box)
        layout.addWidget(
            _hint("QQ 空间依赖 SnowLuma 的对应动作；失败时先确认 SnowLuma 已登录且版本不低于 2.2.1。")
        )
        return page

    # ---------- 高级 ----------

    def _build_advanced(self) -> QWidget:
        page, layout = self._page()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        form = QFormLayout(host)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.bot_name_edit = QLineEdit()
        self.prefix_edit = QLineEdit()
        self.trigger_combo = QComboBox()
        self.trigger_combo.addItem("需要 @ 或叫名字", "at")
        self.trigger_combo.addItem("群里所有消息都可以接", "all")
        self.reply_chance = QDoubleSpinBox()
        self.reply_chance.setRange(0.0, 1.0)
        self.reply_chance.setSingleStep(0.01)
        self.reply_chance.setDecimals(2)
        self.max_replies = QSpinBox()
        self.max_replies.setRange(1, 60)
        self.max_chars = QSpinBox()
        self.max_chars.setRange(40, 1000)
        self.face_ids_edit = QLineEdit()
        self.face_ids_edit.setPlaceholderText("14,13,178,179")
        self.face_chance = QDoubleSpinBox()
        self.face_chance.setRange(0.0, 1.0)
        self.face_chance.setSingleStep(0.01)
        self.face_chance.setDecimals(2)
        self.sticker_chance = QDoubleSpinBox()
        self.sticker_chance.setRange(0.0, 1.0)
        self.sticker_chance.setSingleStep(0.01)
        self.sticker_chance.setDecimals(2)
        self.sticker_paths_edit = QPlainTextEdit()
        self.sticker_paths_edit.setFixedHeight(80)
        self.sticker_paths_edit.setPlaceholderText("每行一个本地图片路径")

        self.custom_face_enabled = QCheckBox("AI 回复时按概率附带一个收藏表情")
        self.custom_face_probability = QDoubleSpinBox()
        self.custom_face_probability.setRange(0.0, 1.0)
        self.custom_face_probability.setSingleStep(0.01)
        self.custom_face_probability.setDecimals(2)
        self.custom_face_list = QListWidget()
        self.custom_face_list.setFixedHeight(120)
        self.custom_face_status = _hint("还没有读取收藏表情。")
        custom_face_box = QWidget()
        custom_face_layout = QVBoxLayout(custom_face_box)
        custom_face_layout.setContentsMargins(0, 0, 0, 0)
        custom_face_layout.setSpacing(6)
        custom_face_layout.addWidget(self.custom_face_list)
        custom_face_row = QHBoxLayout()
        refresh_custom_faces_btn = QPushButton("读取收藏表情")
        refresh_custom_faces_btn.setObjectName("ghost")
        refresh_custom_faces_btn.clicked.connect(self._refresh_custom_faces)
        custom_face_row.addWidget(self.custom_face_status, 1)
        custom_face_row.addWidget(refresh_custom_faces_btn)
        custom_face_layout.addLayout(custom_face_row)

        self.auto_face = QCheckBox("自动附带 QQ 表情")
        self.local_echo = QCheckBox("把发出的消息在桌宠气泡里复述")
        self.announce = QCheckBox("连接失败时让桌宠提醒")
        self.vision_enabled = QCheckBox("识别聊天里的图片（需要 AI 接口支持视觉）")
        self.memory_enabled = QCheckBox("启用长期记忆")
        self.slang_enabled = QCheckBox("启用黑话库")
        self.habit_enabled = QCheckBox("统计聊天习惯")

        self.memory_interval = QSpinBox()
        self.memory_interval.setRange(5, 1440)
        self.memory_interval.setSuffix(" 分钟")
        self.memory_min_batch = QSpinBox()
        self.memory_min_batch.setRange(3, 100)
        self.memory_batch = QSpinBox()
        self.memory_batch.setRange(10, 200)
        self.memory_max = QSpinBox()
        self.memory_max.setRange(10, 500)
        self.slang_max = QSpinBox()
        self.slang_max.setRange(20, 1000)

        form.addRow("机器人名字", self.bot_name_edit)
        form.addRow("指令前缀", self.prefix_edit)
        form.addRow("群聊触发", self.trigger_combo)
        form.addRow("主动接话概率", self.reply_chance)
        form.addRow("每分钟回复上限", self.max_replies)
        form.addRow("回复字数上限", self.max_chars)
        form.addRow("", self.auto_face)
        form.addRow("表情编号", self.face_ids_edit)
        form.addRow("表情附带概率", self.face_chance)
        form.addRow("表情包概率", self.sticker_chance)
        form.addRow("表情包路径", self.sticker_paths_edit)
        form.addRow("", self.custom_face_enabled)
        form.addRow("收藏表情概率", self.custom_face_probability)
        form.addRow("可用收藏表情", custom_face_box)
        form.addRow("", self.local_echo)
        form.addRow("", self.announce)
        form.addRow("", self.vision_enabled)
        form.addRow("", self.memory_enabled)
        form.addRow("", self.slang_enabled)
        form.addRow("", self.habit_enabled)
        form.addRow("记忆整理间隔", self.memory_interval)
        form.addRow("最少攒多少条", self.memory_min_batch)
        form.addRow("每次整理条数", self.memory_batch)
        form.addRow("记忆上限", self.memory_max)
        form.addRow("黑话上限", self.slang_max)

        scroll.setWidget(host)
        layout.addWidget(scroll, 1)
        return page

    # ---------- 语音 ----------

    def _build_voice(self) -> QWidget:
        page, layout = self._page()

        pack_box = QGroupBox("语音包")
        pack_layout = QVBoxLayout(pack_box)
        pack_layout.setSpacing(8)
        pack_hint = _hint(
            "语音包就是装着 mp3 / wav / amr / silk 等音频的文件夹。"
            "SnowLuma 会自动转成 QQ 语音格式；发送前请确认语音包来源合法。"
        )
        pack_layout.addWidget(pack_hint)
        self.voice_pack_list = QListWidget()
        self.voice_pack_list.setFixedHeight(135)
        self.voice_pack_list.currentItemChanged.connect(
            lambda *_: self._refresh_voice_clips()
        )
        pack_layout.addWidget(self.voice_pack_list)
        pack_btns = QHBoxLayout()
        import_btn = QPushButton("导入文件夹")
        import_btn.clicked.connect(self._import_voice_pack)
        scan_btn = QPushButton("扫描电脑")
        scan_btn.clicked.connect(self._scan_voice_packs)
        remove_btn = QPushButton("移除")
        remove_btn.clicked.connect(self._remove_voice_pack)
        open_btn = QPushButton("打开目录")
        open_btn.setObjectName("ghost")
        open_btn.clicked.connect(self._open_selected_voice_pack)
        pack_btns.addWidget(import_btn)
        pack_btns.addWidget(scan_btn)
        pack_btns.addWidget(remove_btn)
        pack_btns.addStretch(1)
        pack_btns.addWidget(open_btn)
        pack_layout.addLayout(pack_btns)
        self.voice_pack_status = _hint("还没有导入语音包。")
        pack_layout.addWidget(self.voice_pack_status)
        layout.addWidget(pack_box)

        clip_box = QGroupBox("语音片段")
        clip_layout = QVBoxLayout(clip_box)
        clip_layout.setSpacing(8)
        self.voice_clip_list = QListWidget()
        self.voice_clip_list.setMinimumHeight(150)
        clip_layout.addWidget(self.voice_clip_list, 1)
        clip_btns = QHBoxLayout()
        preview_btn = QPushButton("试听")
        preview_btn.clicked.connect(self._preview_voice_clip)
        send_clip_btn = QPushButton("发送选中")
        send_clip_btn.setObjectName("primary")
        send_clip_btn.clicked.connect(self._send_voice_clip)
        random_btn = QPushButton("随机发一条")
        random_btn.clicked.connect(self._send_random_voice)
        clip_btns.addWidget(preview_btn)
        clip_btns.addWidget(send_clip_btn)
        clip_btns.addStretch(1)
        clip_btns.addWidget(random_btn)
        clip_layout.addLayout(clip_btns)
        layout.addWidget(clip_box, 1)

        send_box = QGroupBox("发送对象")
        send_layout = QHBoxLayout(send_box)
        self.voice_target = QComboBox()
        self.voice_target.setEditable(True)
        self.voice_target.setMinimumWidth(280)
        if self.voice_target.lineEdit() is not None:
            self.voice_target.lineEdit().setPlaceholderText(
                "从白名单选择，或输入 private:QQ号 / group:群号"
            )
        target_btn = QPushButton("刷新对象")
        target_btn.setObjectName("ghost")
        target_btn.clicked.connect(self._refresh_voice_targets)
        send_layout.addWidget(QLabel("发给"))
        send_layout.addWidget(self.voice_target, 1)
        send_layout.addWidget(target_btn)
        layout.addWidget(send_box)

        auto_box = QGroupBox("自动语音")
        auto_layout = QVBoxLayout(auto_box)
        self.voice_reply_enabled = QCheckBox("AI 回复时按概率附带一条语音")
        self.voice_reply_probability = QDoubleSpinBox()
        self.voice_reply_probability.setRange(0.0, 1.0)
        self.voice_reply_probability.setSingleStep(0.01)
        self.voice_reply_probability.setDecimals(2)
        self.voice_reply_probability.setPrefix("概率 ")
        prob_row = QHBoxLayout()
        prob_row.addWidget(self.voice_reply_probability)
        prob_row.addStretch(1)
        auto_layout.addWidget(self.voice_reply_enabled)
        auto_layout.addLayout(prob_row)
        auto_layout.addWidget(
            _hint(
                "AI 在回复末尾写 [voice] 时会发语音；关闭自动语音后，仍可以用下面的按钮手动发。"
            )
        )
        layout.addWidget(auto_box)
        layout.addStretch(1)
        return page

    # ---------- 诊断 ----------

    def _build_diagnostics(self) -> QWidget:
        page, layout = self._page()
        grid_box = QGroupBox("运行状态")
        grid = QGridLayout(grid_box)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.diag_state = QLabel("")
        self.diag_http = QLabel("")
        self.diag_event = QLabel("")
        self.diag_audience = QLabel("")
        self.diag_data = QLabel("")
        for row, (name, widget) in enumerate(
            (
                ("连接", self.diag_state),
                ("SnowLuma", self.diag_http),
                ("事件地址", self.diag_event),
                ("白名单", self.diag_audience),
                ("数据", self.diag_data),
            )
        ):
            key = QLabel(name)
            key.setObjectName("hint")
            grid.addWidget(key, row, 0)
            grid.addWidget(widget, row, 1)
        grid.setColumnStretch(1, 1)
        layout.addWidget(grid_box)

        test_box = QGroupBox("测试发送")
        test_layout = QHBoxLayout(test_box)
        self.send_target = QLineEdit()
        self.send_target.setPlaceholderText("private:QQ号 或 group:群号")
        self.send_text = QLineEdit()
        self.send_text.setPlaceholderText("要发送的内容")
        send_btn = QPushButton("发送")
        send_btn.setObjectName("primary")
        send_btn.clicked.connect(self._send_test)
        test_layout.addWidget(self.send_target, 1)
        test_layout.addWidget(self.send_text, 2)
        test_layout.addWidget(send_btn)
        layout.addWidget(test_box)

        log_box = QGroupBox("插件日志")
        log_layout = QVBoxLayout(log_box)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(180)
        log_layout.addWidget(self.log_view)
        row = QHBoxLayout()
        refresh_btn = QPushButton("刷新状态和日志")
        refresh_btn.clicked.connect(self._refresh_diagnostics)
        probe_btn = QPushButton("检测连接")
        probe_btn.clicked.connect(self._probe)
        row.addStretch(1)
        row.addWidget(probe_btn)
        row.addWidget(refresh_btn)
        log_layout.addLayout(row)
        layout.addWidget(log_box)
        return page

    # ---------- 数据填充 ----------

    def _fill_custom_faces(
        self, ids: list[str], faces: list[dict[str, str]] | None = None
    ) -> None:
        known = {
            str(item.get("emoji_id", "")): item
            for item in (faces or [])
            if item.get("emoji_id")
        }
        ordered = list(known)
        for emoji_id in ids:
            if emoji_id and emoji_id not in known:
                ordered.append(emoji_id)
                known[emoji_id] = {"emoji_id": emoji_id, "desc": ""}
        self.custom_face_list.clear()
        for emoji_id in ordered:
            desc = str(known.get(emoji_id, {}).get("desc", "")).strip()
            item = QListWidgetItem(f"{desc}  ·  {emoji_id}" if desc else emoji_id)
            item.setData(Qt.ItemDataRole.UserRole, emoji_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = not ids or emoji_id in ids
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            self.custom_face_list.addItem(item)

    def _refresh_custom_faces(self) -> None:
        current = [
            str(self.custom_face_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.custom_face_list.count())
            if self.custom_face_list.item(index) is not None
            and self.custom_face_list.item(index).checkState()
            == Qt.CheckState.Checked
        ]
        if not current:
            current = list(self.service.settings["custom_face_ids"])
        self.custom_face_status.setText("正在读取…")

        def done(faces: list[dict[str, str]]) -> None:
            self._fill_custom_faces(current, faces)
            if faces:
                self.custom_face_status.setText(f"共 {len(faces)} 个收藏表情。")
            else:
                self.custom_face_status.setText(
                    "没有读到收藏表情，确认 SnowLuma 已登录且版本支持该动作。"
                )

        self.service.refresh_custom_faces_async(done)

    def _load_settings(self) -> None:
        settings = self.service.settings
        self.http_edit.setText(settings["snowluma_http"])
        self.token_edit.setText(settings["access_token"])
        self.owner_edit.setText(settings["owner_qq"])
        self.host_edit.setText(settings["http_host"])
        self.port_spin.setValue(int(settings["http_port"]))
        self.path_edit.setText(settings["event_path"])
        self.secret_edit.setText(settings["event_secret"])
        self.auto_start_snowluma.setChecked(bool(settings["auto_start_snowluma"]))
        self.snowluma_dir_edit.setText(settings["snowluma_dir"])

        self.bot_name_edit.setText(settings["bot_name"])
        self.prefix_edit.setText(settings["command_prefix"])
        index = self.trigger_combo.findData(settings["group_trigger"])
        self.trigger_combo.setCurrentIndex(max(0, index))
        self.reply_chance.setValue(float(settings["reply_chance"]))
        self.max_replies.setValue(int(settings["max_replies_per_minute"]))
        self.max_chars.setValue(int(settings["max_reply_chars"]))
        self.auto_face.setChecked(bool(settings["auto_face"]))
        self.face_ids_edit.setText(",".join(settings["face_ids"]))
        self.face_chance.setValue(float(settings["face_probability"]))
        self.sticker_chance.setValue(float(settings["sticker_probability"]))
        self.sticker_paths_edit.setPlainText("\n".join(settings["sticker_paths"]))
        self.custom_face_enabled.setChecked(bool(settings["custom_face_enabled"]))
        self.custom_face_probability.setValue(
            float(settings["custom_face_probability"])
        )
        self._fill_custom_faces(
            list(settings["custom_face_ids"]), self.service.custom_face_items()
        )
        self.local_echo.setChecked(bool(settings["local_echo"]))
        self.announce.setChecked(bool(settings["announce"]))
        self.vision_enabled.setChecked(bool(settings["vision_enabled"]))
        self.memory_enabled.setChecked(bool(settings["memory_enabled"]))
        self.slang_enabled.setChecked(bool(settings["slang_enabled"]))
        self.habit_enabled.setChecked(bool(settings["habit_enabled"]))
        self.memory_interval.setValue(int(settings["memory_interval_minutes"]))
        self.memory_min_batch.setValue(int(settings["memory_min_batch"]))
        self.memory_batch.setValue(int(settings["memory_batch_size"]))
        self.memory_max.setValue(int(settings["memory_max_items"]))
        self.slang_max.setValue(int(settings["slang_max_terms"]))

        self.qzone_enabled.setChecked(bool(settings["qzone_enabled"]))
        perm_index = self.qzone_perm.findData(settings["qzone_permission"])
        self.qzone_perm.setCurrentIndex(max(0, perm_index))
        self.qzone_targets.setText(",".join(settings["qzone_target_uins"]))
        self.qzone_auto_reply.setChecked(bool(settings["qzone_auto_reply"]))
        self.qzone_reply_interval.setValue(int(settings["qzone_reply_interval"]))
        self.qzone_reply_max.setValue(int(settings["qzone_reply_max"]))

        self.voice_reply_enabled.setChecked(bool(settings["voice_reply_enabled"]))
        self.voice_reply_probability.setValue(float(settings["voice_reply_probability"]))
        self._refresh_voice_packs()
        self._refresh_voice_targets()

        self.persona_edit.setPlainText(self.service.persona_text())
        self._update_persona_count()

    def _collect_settings(self) -> dict[str, Any]:
        face_ids = [
            "".join(ch for ch in part if ch.isdigit())
            for part in re.split(r"[,，\s]+", self.face_ids_edit.text())
        ]
        custom_face_ids = [
            str(self.custom_face_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.custom_face_list.count())
            if self.custom_face_list.item(index) is not None
            and self.custom_face_list.item(index).checkState()
            == Qt.CheckState.Checked
        ]
        if self.custom_face_list.count() == 0:
            custom_face_ids = list(self.service.settings["custom_face_ids"])
        return {
            "snowluma_http": self.http_edit.text().strip(),
            "access_token": self.token_edit.text().strip(),
            "owner_qq": "".join(ch for ch in self.owner_edit.text() if ch.isdigit()),
            "http_host": self.host_edit.text().strip() or "127.0.0.1",
            "http_port": self.port_spin.value(),
            "event_path": self.path_edit.text().strip() or "/onebot",
            "event_secret": self.secret_edit.text().strip(),
            "auto_start_snowluma": self.auto_start_snowluma.isChecked(),
            "snowluma_dir": self.snowluma_dir_edit.text().strip(),
            "bot_name": self.bot_name_edit.text().strip() or "小猫",
            "command_prefix": self.prefix_edit.text().strip() or "/",
            "group_trigger": self.trigger_combo.currentData(),
            "reply_chance": self.reply_chance.value(),
            "max_replies_per_minute": self.max_replies.value(),
            "max_reply_chars": self.max_chars.value(),
            "auto_face": self.auto_face.isChecked(),
            "face_ids": [item for item in face_ids if item],
            "face_probability": self.face_chance.value(),
            "sticker_probability": self.sticker_chance.value(),
            "sticker_paths": [
                line.strip()
                for line in self.sticker_paths_edit.toPlainText().splitlines()
                if line.strip()
            ],
            "custom_face_enabled": self.custom_face_enabled.isChecked(),
            "custom_face_probability": self.custom_face_probability.value(),
            "custom_face_ids": custom_face_ids,
            "local_echo": self.local_echo.isChecked(),
            "announce": self.announce.isChecked(),
            "vision_enabled": self.vision_enabled.isChecked(),
            "memory_enabled": self.memory_enabled.isChecked(),
            "slang_enabled": self.slang_enabled.isChecked(),
            "habit_enabled": self.habit_enabled.isChecked(),
            "memory_interval_minutes": self.memory_interval.value(),
            "memory_min_batch": self.memory_min_batch.value(),
            "memory_batch_size": self.memory_batch.value(),
            "memory_max_items": self.memory_max.value(),
            "slang_max_terms": self.slang_max.value(),
            "watch_users": [
                str(self.watch_list.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.watch_list.count())
                if self.watch_list.item(index) is not None
            ],
            "qzone_enabled": self.qzone_enabled.isChecked(),
            "qzone_permission": self.qzone_perm.currentData(),
            "qzone_target_uins": [
                "".join(ch for ch in part if ch.isdigit())
                for part in re.split(r"[,，\s]+", self.qzone_targets.text())
                if "".join(ch for ch in part if ch.isdigit())
            ],
            "qzone_auto_reply": self.qzone_auto_reply.isChecked(),
            "qzone_reply_interval": self.qzone_reply_interval.value(),
            "qzone_reply_max": self.qzone_reply_max.value(),
            "voice_packs": [
                str(self.voice_pack_list.item(index).data(Qt.ItemDataRole.UserRole))
                for index in range(self.voice_pack_list.count())
                if self.voice_pack_list.item(index) is not None
            ],
            "voice_reply_enabled": self.voice_reply_enabled.isChecked(),
            "voice_reply_probability": self.voice_reply_probability.value(),
        }

    def _refresh_all(self) -> None:
        self._refresh_status()
        self._refresh_whitelist()
        self._refresh_memory()
        self._refresh_slang()
        self._refresh_habits()
        self._refresh_voice_packs()
        self._refresh_voice_targets()
        self._refresh_diagnostics()

    def _refresh_status(self) -> None:
        snapshot = self.service.snapshot()
        connection = snapshot["connection_ok"]
        if not self.service.started:
            self.state_label.setText("● 未运行")
            self.state_label.setToolTip("插件没有在运行，先到插件管理里启用。")
            self.state_label.setStyleSheet(f"color: {MUTED};")
        else:
            if connection is True:
                text, color = "已连接", OK
            elif connection is False:
                text, color = "连接失败", ERROR
            else:
                text, color = "等待检查", WARN
            port = snapshot["event_port"]
            port_text = f"端口 {port}" if port else "端口未启动"
            self.state_label.setText(f"● {text} · {port_text}")
            self.state_label.setToolTip(
                f"SnowLuma：{snapshot['http']}\n事件地址：{snapshot['event_url']}"
            )
            self.state_label.setStyleSheet(f"color: {color};")
        self.event_url_label.setText(f"把 SnowLuma 的事件上报地址填成：{snapshot['event_url']}")
        managed = snapshot.get("snowluma_managed") or {}
        if managed.get("managed"):
            auto_text = f"插件已启动 SnowLuma（PID {managed.get('pid')}）。"
        elif not snapshot.get("auto_start_snowluma"):
            auto_text = "自动启动已关闭。"
        elif managed.get("external"):
            auto_text = "SnowLuma 已在运行，插件没有接管它的进程。"
        else:
            auto_text = "等待自动启动，或 SnowLuma 已经在别处运行。"
        self.auto_state_label.setText(auto_text)
        self.owner_label.setText(
            f"主人 QQ：{snapshot['owner_qq'] or '未设置'}（主人消息会自动放行）"
        )
        if self.diag_state is not None:
            state_text = {True: "已连接", False: "连接失败", None: "还没检查"}.get(
                connection, "还没检查"
            )
            self.diag_state.setText(state_text)
            self.diag_http.setText(snapshot["http"])
            self.diag_event.setText(snapshot["event_url"])
            self.diag_audience.setText(
                f"好友 {snapshot['private_count']} 个，群 {snapshot['group_count']} 个，"
                f"盯人 {snapshot.get('watch_count', 0)} 个"
            )
            self.diag_data.setText(
                f"记忆 {snapshot['memory_count']} 条，黑话 {snapshot['slang_count']} 条，"
                f"语音包 {snapshot['voice_pack_count']} 个，"
                f"收藏表情 {snapshot.get('custom_face_count', 0)} 个，"
                f"QQ 空间{'开启' if snapshot['qzone_enabled'] else '关闭'}"
            )
        last = float(snapshot.get("qzone_last_check", 0) or 0)
        stamp = time.strftime("%m-%d %H:%M", time.localtime(last)) if last else "还没检查"
        if hasattr(self, "qzone_auto_status"):
            self.qzone_auto_status.setText(
                f"已回复 {snapshot.get('qzone_replied_count', 0)} 条评论；上次检查：{stamp}"
            )

    def _refresh_whitelist(self) -> None:
        items = self.service.allow_items()
        notes = self.service.allow_notes()
        for widget, kind in (
            (self.private_list, "private"),
            (self.group_list, "group"),
        ):
            widget.blockSignals(True)
            widget.clear()
            for value in items[kind]:
                note = notes.get(f"{kind}:{value}", "")
                item = QListWidgetItem(f"{value}  ·  {note}" if note else value)
                item.setData(Qt.ItemDataRole.UserRole, value)
                if note:
                    item.setToolTip(note)
                widget.addItem(item)
            widget.blockSignals(False)
        self.private_note.clear()
        self.group_note.clear()
        self._refresh_watch()

    def _refresh_watch(self) -> None:
        if not hasattr(self, "watch_list"):
            return
        self.watch_list.clear()
        for value in self.service.watch_items():
            item = QListWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, value)
            self.watch_list.addItem(item)

    def _refresh_memory(self) -> None:
        self.memory_list.clear()
        for item in self.service.memory_items(200):
            text = " ".join(str(item.get("text", "")).split())[:120]
            widget_item = QListWidgetItem(f"{text}  ·  {int(item.get('importance', 0))}")
            widget_item.setData(Qt.ItemDataRole.UserRole, str(item.get("id", "")))
            self.memory_list.addItem(widget_item)
        self.memory_status.setText(f"共 {self.memory_list.count()} 条长期记忆。")

    def _refresh_slang(self) -> None:
        terms = self.service.slang_items()
        self.slang_table.setRowCount(len(terms))
        for row, item in enumerate(terms):
            self.slang_table.setItem(
                row, 0, QTableWidgetItem(str(item.get("term", "")))
            )
            self.slang_table.setItem(
                row, 1, QTableWidgetItem(str(item.get("meaning", "")))
            )
            self.slang_table.setItem(
                row, 2, QTableWidgetItem(str(int(item.get("count", 0))))
            )

    def _refresh_habits(self) -> None:
        rows = self.service.habit_rows()
        self.habit_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            conversation = str(item.get("conversation", ""))
            updated = float(item.get("updated_at", 0) or 0)
            stamp = (
                time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "-"
            )
            values = [
                conversation,
                str(item.get("count", 0)),
                f"{float(item.get('short_rate', 0)) * 100:.0f}%",
                str(item.get("question", 0)),
                str(item.get("faces", 0)),
                stamp,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setData(Qt.ItemDataRole.UserRole, conversation)
                self.habit_table.setItem(row, column, cell)

    def _refresh_diagnostics(self) -> None:
        self._refresh_status()
        self.log_view.setPlainText(self.service.log_tail(40) or "还没有日志。")

    def _update_persona_count(self) -> None:
        self.persona_count.setText(f"{len(self.persona_edit.toPlainText())} / 8000 字")

    # ---------- 操作 ----------

    def _notify(self, text: str, ok: bool = True) -> None:
        color = OK if ok else ERROR
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {color}; font-size: 11px;")

    def _apply_settings(self, silent: bool = False) -> None:
        result = self.service.apply_settings(self._collect_settings())
        self._load_settings()
        self._refresh_all()
        if not silent or not result.get("ok"):
            self._notify(str(result.get("message", "")), bool(result.get("ok")))

    def _probe(self) -> None:
        self.test_result.setText("正在检测…")
        self.test_result.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

        def done(ok: bool, text: str) -> None:
            self.test_result.setText(text)
            self.test_result.setStyleSheet(
                f"color: {OK if ok else ERROR}; font-size: 11px;"
            )
            self._refresh_status()

        self.service.probe(done)

    def _copy_event_url(self) -> None:
        snapshot = self.service.snapshot()
        QGuiApplication.clipboard().setText(snapshot["event_url"])
        self._notify("事件地址已复制。")

    def _detect_snowluma_dir(self) -> None:
        from .snowluma import detect_snowluma_dir

        folder = detect_snowluma_dir(self.snowluma_dir_edit.text().strip())
        if folder is None:
            self._notify("没找到 SnowLuma，请手动选择包含 index.mjs 的文件夹。", False)
            return
        self.snowluma_dir_edit.setText(str(folder))
        self._notify(f"已找到 SnowLuma：{folder}")

    def _browse_snowluma_dir(self) -> None:
        start = self.snowluma_dir_edit.text().strip() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择 SnowLuma 目录", start)
        if folder:
            self.snowluma_dir_edit.setText(folder)

    def _takeover_snowluma(self) -> None:
        answer = QMessageBox.question(
            self,
            "接管 SnowLuma",
            "会结束当前正在运行的 SnowLuma（node.exe），再由插件在后台重新启动。\n"
            "QQ 登录状态保存在 SnowLuma 自己的配置里，不会丢失。确定继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._notify("正在结束旧的 SnowLuma 并重新启动…")

        def done(text: str) -> None:
            self._notify(text)
            self._refresh_status()

        self.service.takeover_snowluma(done)

    def _allow_target_value(self, kind: str) -> str:
        widget = self.private_list if kind == "private" else self.group_list
        current = widget.currentItem()
        if current is None:
            return ""
        value = str(current.data(Qt.ItemDataRole.UserRole) or "")
        return value or current.text().split("  ·  ")[0].strip()

    def _load_allow_note(self, kind: str) -> None:
        target = self._allow_target_value(kind)
        note = self.private_note if kind == "private" else self.group_note
        note.setText(self.service.allow_note(kind, target) if target else "")

    def _select_allow_target(self, kind: str, target: str) -> None:
        widget = self.private_list if kind == "private" else self.group_list
        for row in range(widget.count()):
            item = widget.item(row)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == target:
                widget.setCurrentRow(row)
                return

    def _save_allow_note(self, kind: str) -> None:
        target = self._allow_target_value(kind)
        if not target:
            self._notify("先选中要加备注的好友或群。", False)
            return
        note = self.private_note if kind == "private" else self.group_note
        text = self.service.set_allow_note(kind, target, note.text())
        self._refresh_whitelist()
        self._select_allow_target(kind, target)
        self._notify(text, text.startswith("已"))

    def _allow_change(self, kind: str, add: bool) -> None:
        if add:
            source = self.private_input if kind == "private" else self.group_input
            note_input = self.private_note if kind == "private" else self.group_note
            target = source.text().strip()
            if not target:
                self._notify("先填 QQ 号或群号。", False)
                return
            text = self.service.allow_target_text(
                kind, target, True, note_input.text().strip()
            )
            if not text.startswith("已"):
                self._notify(text, False)
                return
            source.clear()
            note_input.clear()
        else:
            target = self._allow_target_value(kind)
            if not target:
                self._notify("先选中要删除的条目。", False)
                return
            text = self.service.allow_target_text(kind, target, False)
        self._refresh_whitelist()
        self._refresh_status()
        self._notify(text, text.startswith("已"))

    def _watch_add(self) -> None:
        target = self.watch_input.text().strip()
        if not target:
            self._notify("先填要盯的 QQ 号。", False)
            return
        message = self.service.watch_add(target)
        if message.startswith("已"):
            self.watch_input.clear()
        self._refresh_watch()
        self._refresh_status()
        self._notify(message, message.startswith("已"))

    def _watch_remove(self) -> None:
        current = self.watch_list.currentItem()
        if current is None:
            self._notify("先选中要取消的 QQ 号。", False)
            return
        value = str(current.data(Qt.ItemDataRole.UserRole) or current.text())
        message = self.service.watch_remove(value)
        self._refresh_watch()
        self._refresh_status()
        self._notify(message, message.startswith("已"))

    def _save_persona(self) -> None:
        try:
            message = self.service.set_persona_text(self.persona_edit.toPlainText())
        except ValueError as exc:
            self._notify(str(exc), False)
            return
        self._notify(message)

    def _reset_persona(self) -> None:
        message = self.service.reset_persona()
        self.persona_edit.setPlainText(self.service.persona_text())
        self._notify(message)

    def _add_memory(self) -> None:
        text = self.memory_input.text().strip()
        if not text:
            self._notify("先输入要记住的内容。", False)
            return
        message = self.service.add_memory_text(text)
        self.memory_input.clear()
        self._refresh_memory()
        self._refresh_status()
        self._notify(message, message.startswith("记住"))

    def _delete_memory(self) -> None:
        current = self.memory_list.currentItem()
        if current is None:
            self._notify("先选中要删除的记忆。", False)
            return
        keyword = str(current.data(Qt.ItemDataRole.UserRole) or current.text())
        message = self.service.remove_memory_text(keyword)
        self._refresh_memory()
        self._refresh_status()
        self._notify(message)

    def _compact_memory(self) -> None:
        self.memory_status.setText("正在整理聊天记录…")

        def done(text: str) -> None:
            self._refresh_memory()
            self._refresh_slang()
            self._refresh_status()
            self._notify(text, not text.startswith("整理失败"))

        self.service.compact_now(done)

    def _delete_habit(self) -> None:
        row = self.habit_table.currentRow()
        if row < 0:
            self._notify("先选中要删除的会话。", False)
            return
        cell = self.habit_table.item(row, 0)
        conversation = str(cell.data(Qt.ItemDataRole.UserRole) or cell.text()) if cell else ""
        if not conversation:
            self._notify("没读到会话，刷新后再试。", False)
            return
        answer = QMessageBox.question(
            self,
            "删除聊天习惯",
            f"删除 {conversation} 的习惯统计？\n长期记忆和黑话库不受影响。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        message = self.service.remove_habit(conversation)
        self._refresh_habits()
        self._refresh_status()
        self._notify(message, message.startswith("已"))

    def _add_slang(self) -> None:
        term = self.slang_term.text().strip()
        if not term:
            self._notify("先填词。", False)
            return
        message = self.service.add_slang_text(term, self.slang_meaning.text().strip())
        self.slang_term.clear()
        self.slang_meaning.clear()
        self._refresh_slang()
        self._refresh_status()
        self._notify(message, message.startswith("收进"))

    def _delete_slang(self) -> None:
        row = self.slang_table.currentRow()
        if row < 0:
            self._notify("先选中要删除的词。", False)
            return
        item = self.slang_table.item(row, 0)
        if item is None:
            return
        message = self.service.remove_slang_text(item.text())
        self._refresh_slang()
        self._refresh_status()
        self._notify(message)

    def _publish_qzone(self) -> None:
        text = self.qzone_text.toPlainText().strip()
        if not text:
            self._notify("说说内容为空。", False)
            return
        self._apply_settings(silent=True)
        self.qzone_result.setPlainText("正在发布…")

        def done(ok: bool, message: str) -> None:
            self.qzone_result.setPlainText(message)
            self._notify(message, ok)

        self.service.qzone_action("publish", text, done)

    def _delete_qzone(self) -> None:
        tid = self.qzone_tid.text().strip()
        if not tid:
            self._notify("先填说说 id。", False)
            return

        def done(ok: bool, message: str) -> None:
            self.qzone_result.setPlainText(message)
            self._notify(message, ok)

        self.service.qzone_action("delete", tid, done)

    def _qzone_action(self, action: str) -> None:
        self.qzone_result.setPlainText("正在读取…")

        def done(ok: bool, message: str) -> None:
            self.qzone_result.setPlainText(message)
            self._notify(message if ok else message, ok)

        self.service.qzone_action(action, "", done)

    def _send_test(self) -> None:
        target = self.send_target.text().strip()
        text = self.send_text.text().strip()
        if not target or not text:
            self._notify("目标或内容为空。", False)
            return

        def done(ok: bool, message: str) -> None:
            self._notify(message, ok)

        self.service.send_test(target, text, done)

    # ---------- 语音操作 ----------

    def _selected_voice_pack_path(self) -> str:
        item = self.voice_pack_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _refresh_voice_packs(self) -> None:
        current = self._selected_voice_pack_path()
        packs = self.service.voice_pack_list()
        self.voice_pack_list.blockSignals(True)
        self.voice_pack_list.clear()
        restore = -1
        for index, pack in enumerate(packs):
            item = QListWidgetItem(f"{pack['name']}（{pack['count']} 条）")
            item.setData(Qt.ItemDataRole.UserRole, pack["path"])
            item.setToolTip(pack["path"])
            self.voice_pack_list.addItem(item)
            if pack["path"] == current:
                restore = index
        self.voice_pack_list.blockSignals(False)
        if packs:
            self.voice_pack_list.setCurrentRow(restore if restore >= 0 else 0)
            self.voice_pack_status.setText(f"已导入 {len(packs)} 个语音包。")
        else:
            self.voice_pack_status.setText("还没有导入语音包。可以点“导入文件夹”或“扫描电脑”。")
        self._refresh_voice_clips()

    def _refresh_voice_clips(self) -> None:
        self.voice_clip_list.clear()
        folder = self._selected_voice_pack_path()
        if not folder:
            return
        try:
            clips = self.service.voice_clips(folder)
        except Exception as exc:
            self._notify(f"读取语音包失败：{exc}", False)
            return
        for clip in clips[:600]:
            path = Path(clip)
            item = QListWidgetItem(path.stem if path.stem else path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self.voice_clip_list.addItem(item)
        if len(clips) > 600:
            self.voice_pack_status.setText(
                f"这个语音包有 {len(clips)} 条，列表只显示前 600 条；随机发送会覆盖全部。"
            )

    def _refresh_voice_targets(self) -> None:
        current = self.voice_target.currentText().strip()
        self.voice_target.blockSignals(True)
        self.voice_target.clear()
        items = self.service.allow_items()
        for value in items["private"]:
            self.voice_target.addItem(f"好友 {value}", f"private:{value}")
        for value in items["group"]:
            self.voice_target.addItem(f"群 {value}", f"group:{value}")
        self.voice_target.setCurrentIndex(-1)
        self.voice_target.setEditText(current)
        self.voice_target.blockSignals(False)

    def _voice_target_value(self) -> str:
        text = self.voice_target.currentText().strip()
        data = self.voice_target.currentData()
        index = self.voice_target.currentIndex()
        if (
            data
            and index >= 0
            and text == self.voice_target.itemText(index)
        ):
            return str(data)
        return text

    def _import_voice_pack(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择语音包文件夹", str(Path.home())
        )
        if not folder:
            return
        message = self.service.add_voice_packs([folder])
        self._refresh_voice_packs()
        self._notify(message, message.startswith("已导入"))

    def _scan_voice_packs(self) -> None:
        self._notify("正在扫描下载、桌面、文档、音乐等目录…")
        self.voice_pack_status.setText("扫描中，请稍等…")

        def done(packs: list[dict[str, Any]]) -> None:
            if not packs:
                self.voice_pack_status.setText(
                    "没找到语音包。可以用“导入文件夹”手动选择。"
                )
                return
            message = self.service.add_voice_packs(
                [str(item.get("path", "")) for item in packs]
            )
            self._refresh_voice_packs()
            self._notify(message, message.startswith("已导入"))

        self.service.scan_voice_packs(done)

    def _remove_voice_pack(self) -> None:
        folder = self._selected_voice_pack_path()
        if not folder:
            self._notify("先选中要移除的语音包。", False)
            return
        message = self.service.remove_voice_pack(folder)
        self._refresh_voice_packs()
        self._notify(message, message.startswith("已移除"))

    def _open_selected_voice_pack(self) -> None:
        folder = self._selected_voice_pack_path()
        if folder and Path(folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self._notify("先选中一个语音包。", False)

    def _selected_voice_clip(self) -> str:
        item = self.voice_clip_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _preview_voice_clip(self) -> None:
        clip = self._selected_voice_clip()
        if not clip or not Path(clip).is_file():
            self._notify("先选中一条语音。", False)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(clip))

    def _send_voice_clip(self) -> None:
        target = self._voice_target_value()
        clip = self._selected_voice_clip()
        if not target:
            self._notify("先选择或填写发送对象。", False)
            return
        if not clip:
            self._notify("先选中一条语音。", False)
            return

        def done(ok: bool, message: str) -> None:
            self._notify(message, ok)

        self.service.send_voice(target, clip, done)

    def _send_random_voice(self) -> None:
        target = self._voice_target_value()
        if not target:
            self._notify("先选择或填写发送对象。", False)
            return

        def done(ok: bool, message: str) -> None:
            self._notify(message, ok)

        self.service.send_random_voice(target, self._selected_voice_pack_path(), done)

    def _open_folder(self) -> None:
        path = Path(str(getattr(self.service, "state_root", "")))
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self._notify("数据目录还没创建。", False)
