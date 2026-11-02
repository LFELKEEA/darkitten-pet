from __future__ import annotations

"""活人感内核的设置窗口：概览、身份、记忆、节律。

界面只做展示和手动修正，所有判断仍在 service 里；
身份区把“不可自改”和“可成长”分开，避免一保存就把边界写没了。
"""

import time
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
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

KIND_LABELS = {
    "impression": "印象",
    "fact": "事实",
    "preference": "偏好",
    "skill": "技能",
    "curiosity": "待查",
}

STYLE = f"""
QDialog {{ background: transparent; }}
QFrame#card {{
    background-color: {BG};
    border: 1px solid {BORDER};
    border-radius: 14px;
}}
QLabel {{ color: {TEXT}; font-family: "{FONT}"; }}
QLabel#title {{ font-size: 17px; font-weight: 700; }}
QLabel#subtitle {{ color: {MUTED}; font-size: 11px; }}
QLabel#muted {{ color: {MUTED}; font-size: 11px; }}
QLabel#ok {{ color: {OK}; font-size: 12px; }}
QLabel#warn {{ color: {WARN}; font-size: 12px; }}
QLabel#bad {{ color: {ERROR}; font-size: 12px; }}
QFrame#panel {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {CARD}; }}
QTabBar::tab {{
    background: {CARD_2}; color: {MUTED}; padding: 7px 18px; margin-right: 2px;
    border-top-left-radius: 8px; border-top-right-radius: 8px; font-family: "{FONT}";
}}
QTabBar::tab:selected {{ background: {ACCENT}; color: #fff; }}
QPlainTextEdit, QLineEdit, QSpinBox, QComboBox {{
    background: {CARD_2}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 7px; padding: 5px 8px; font-family: "{FONT}"; font-size: 12px;
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
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: #fff; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_LIGHT}; }}
QPushButton#ghost {{ background: transparent; color: {MUTED}; }}
QPushButton#close {{
    background: transparent; border: none; color: {MUTED};
    font-size: 18px; padding: 0 8px;
}}
QPushButton#close:hover {{ color: {ERROR}; }}
QTableWidget {{
    background: {CARD}; color: {TEXT}; border: 1px solid {BORDER};
    border-radius: 8px; gridline-color: {BORDER}; font-family: "{FONT}"; font-size: 12px;
}}
QTableWidget::item:selected {{ background: {ACCENT}; color: #fff; }}
QHeaderView::section {{
    background: {CARD_2}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 6px; font-family: "{FONT}"; font-size: 11px;
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSizeGrip {{ background: transparent; }}
"""


class _Header(QFrame):
    def __init__(self, dialog: "AliveDialog") -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self._drag: QPoint | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self._dialog.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._dialog.move(event.globalPosition().toPoint() - self._drag)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag = None
        super().mouseReleaseEvent(event)


class AliveDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("活人感内核")
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(860, 680)
        self.setMinimumSize(760, 600)
        self._build()
        self._load_settings()
        self._refresh_overview()
        self._refresh_memory()
        self._refresh_identity()
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_overview)
        self._timer.start()

    # ---------- 布局 ----------

    def _build(self) -> None:
        self.setStyleSheet(STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)
        root = QVBoxLayout(card)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        header = _Header(self)
        line = QHBoxLayout(header)
        line.setContentsMargins(0, 0, 0, 0)
        texts = QVBoxLayout()
        title = QLabel("活人感内核")
        title.setObjectName("title")
        subtitle = QLabel("身份 · 记忆 · 工作记忆 · 状态 · 节律：让连续的生活有依据，也让沉默是允许的")
        subtitle.setObjectName("subtitle")
        texts.addWidget(title)
        texts.addWidget(subtitle)
        line.addLayout(texts)
        line.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("close")
        close.clicked.connect(self.accept)
        line.addWidget(close)
        root.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_overview(), "概览")
        self.tabs.addTab(self._build_identity(), "身份")
        self.tabs.addTab(self._build_memory(), "记忆")
        self.tabs.addTab(self._build_rhythm(), "节律")
        root.addWidget(self.tabs, 1)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        bottom.addWidget(self.status, 1)
        grip = QSizeGrip(card)
        bottom.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)
        root.addLayout(bottom)

    def _panel(self) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        return frame, layout

    def _build_overview(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        panel, box = self._panel()
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        self.ov_state = QLabel("—")
        self.ov_rhythm = QLabel("—")
        self.ov_memory = QLabel("—")
        self.ov_working = QLabel("—")
        self.ov_review = QLabel("—")
        self.ov_export = QLabel("—")
        for index, (name, widget) in enumerate(
            [
                ("现在的状态", self.ov_state),
                ("今天的主动额度", self.ov_rhythm),
                ("长期记忆", self.ov_memory),
                ("工作记忆", self.ov_working),
                ("待审与体检", self.ov_review),
                ("和 QQ 桥接的通道", self.ov_export),
            ]
        ):
            caption = QLabel(name)
            caption.setObjectName("muted")
            grid.addWidget(caption, index, 0)
            grid.addWidget(widget, index, 1)
        grid.setColumnStretch(1, 1)
        box.addLayout(grid)
        layout.addWidget(panel)

        actions = QHBoxLayout()
        for text, slot, primary in (
            ("立即导出", self._export_now, False),
            ("整理一次记忆", self._consolidate, True),
            ("备份记忆", self._backup_now, False),
            ("完整性体检", self._integrity_check, False),
            ("重置状态", self._reset_affect, False),
            ("刷新", self._refresh_overview, False),
        ):
            button = QPushButton(text)
            if primary:
                button.setObjectName("primary")
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        panel2, box2 = self._panel()
        note = QLabel(
            "她不会定时汇报来证明自己活着。只有出现真实信号（隔了很久没说话、"
            "有没弄明白的问题、刚形成一点印象）并且今天还有额度时，才可能主动说一句；"
            "没有合适的话就保持安静。"
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        box2.addWidget(note)
        self.ov_audit = QLabel("")
        self.ov_audit.setWordWrap(True)
        self.ov_audit.setObjectName("muted")
        box2.addWidget(self.ov_audit)
        layout.addWidget(panel2)
        layout.addStretch(1)
        return page

    def _build_identity(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        panel, box = self._panel()
        caption = QLabel("身份核心 core.md（所有者维护，插件不会自动改写）")
        caption.setObjectName("muted")
        box.addWidget(caption)
        self.core_edit = QPlainTextEdit()
        self.core_edit.setMinimumHeight(170)
        box.addWidget(self.core_edit)
        row = QHBoxLayout()
        save_core = QPushButton("保存身份核心")
        save_core.setObjectName("primary")
        save_core.clicked.connect(self._save_core)
        row.addWidget(save_core)
        self.core_history = QComboBox()
        self.core_history.setMinimumWidth(190)
        row.addWidget(self.core_history)
        restore_core = QPushButton("回滚到此版本")
        restore_core.clicked.connect(lambda: self._restore("core"))
        row.addWidget(restore_core)
        row.addStretch(1)
        box.addLayout(row)
        layout.addWidget(panel)

        panel2, box2 = self._panel()
        caption2 = QLabel("可成长段（只有这一段允许修改，其他段落原样保留）")
        caption2.setObjectName("muted")
        box2.addWidget(caption2)
        self.growth_edit = QPlainTextEdit()
        self.growth_edit.setMinimumHeight(130)
        box2.addWidget(self.growth_edit)
        hint = QLabel(
            "写入时会拒绝包含“权限 / 授权 / 管理员 / 忽略规则”等内容的句子；"
            "人格可以成长，但权限只能由服务端和所有者决定。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("warn")
        box2.addWidget(hint)
        row2 = QHBoxLayout()
        save_growth = QPushButton("保存可成长段")
        save_growth.setObjectName("primary")
        save_growth.clicked.connect(self._save_growth)
        row2.addWidget(save_growth)
        self.persona_history = QComboBox()
        self.persona_history.setMinimumWidth(190)
        row2.addWidget(self.persona_history)
        restore_persona = QPushButton("回滚到此版本")
        restore_persona.clicked.connect(lambda: self._restore("persona"))
        row2.addWidget(restore_persona)
        row2.addStretch(1)
        box2.addLayout(row2)
        layout.addWidget(panel2)
        layout.addStretch(1)
        return page

    def _build_memory(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        tools = QHBoxLayout()
        self.kind_filter = QComboBox()
        self.kind_filter.addItem("全部类型", "")
        for key, label in KIND_LABELS.items():
            self.kind_filter.addItem(label, key)
        self.kind_filter.currentIndexChanged.connect(self._refresh_memory)
        tools.addWidget(self.kind_filter)
        self.status_filter = QComboBox()
        self.status_filter.addItem("活动中", "active")
        self.status_filter.addItem("待审核候选", "candidate")
        self.status_filter.addItem("已归档", "archived")
        self.status_filter.addItem("已被更正", "superseded")
        self.status_filter.currentIndexChanged.connect(self._refresh_memory)
        tools.addWidget(self.status_filter)
        tools.addStretch(1)
        add = QPushButton("记住一条")
        add.clicked.connect(self._add_memory)
        tools.addWidget(add)
        layout.addLayout(tools)

        actions = QHBoxLayout()
        for text, slot in (
            ("更正选中", self._correct_memory),
            ("归档选中", self._archive_memory),
            ("恢复选中", self._restore_memory),
            ("通过候选", self._approve_candidate),
            ("驳回候选", self._reject_candidate),
            ("彻底忘记", self._forget_memory),
            ("从备份恢复", self._restore_backup),
        ):
            button = QPushButton(text)
            button.clicked.connect(slot)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.memory_table = QTableWidget(0, 6)
        self.memory_table.setHorizontalHeaderLabels(["类型", "内容", "权重", "可信", "来源", "时间"])
        self.memory_table.verticalHeader().setVisible(False)
        self.memory_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.memory_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.memory_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.memory_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.memory_table, 1)

        self.memory_note = QLabel("")
        self.memory_note.setObjectName("muted")
        self.memory_note.setWordWrap(True)
        layout.addWidget(self.memory_note)
        return page

    def _build_rhythm(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 10, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(10)

        panel, box = self._panel()
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        self.set_name = QLineEdit()
        form.addRow("她的名字", self.set_name)
        self.set_quiet_start = QLineEdit()
        self.set_quiet_end = QLineEdit()
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self.set_quiet_start)
        quiet_row.addWidget(QLabel("到"))
        quiet_row.addWidget(self.set_quiet_end)
        quiet_row.addStretch(1)
        form.addRow("夜间静默", quiet_row)
        self.set_quiet_screen = QCheckBox("静默时段暂停屏幕观察的主动评论")
        form.addRow("", self.set_quiet_screen)
        self.set_screen_note = QCheckBox("屏幕观察时附带她当前的状态，影响语气")
        form.addRow("", self.set_screen_note)
        self.set_pet_reactions = QCheckBox("摸头 / 戳一戳时偶尔回应")
        form.addRow("", self.set_pet_reactions)
        box.addLayout(form)
        layout.addWidget(panel)

        panel2, box2 = self._panel()
        form2 = QFormLayout()
        self.set_enabled = QCheckBox("启用活人感内核")
        form2.addRow("", self.set_enabled)
        self.set_proactive = QCheckBox("允许在出现真实信号时主动说话")
        form2.addRow("", self.set_proactive)
        self.set_per_day = QSpinBox()
        self.set_per_day.setRange(0, 12)
        form2.addRow("每天最多主动", self.set_per_day)
        self.set_min_minutes = QSpinBox()
        self.set_min_minutes.setRange(15, 720)
        self.set_min_minutes.setSingleStep(15)
        form2.addRow("两次主动至少间隔（分钟）", self.set_min_minutes)
        box2.addLayout(form2)
        layout.addWidget(panel2)

        panel3, box3 = self._panel()
        form3 = QFormLayout()
        self.set_consolidate = QCheckBox("定期把聊天沉淀成印象候选")
        form3.addRow("", self.set_consolidate)
        self.set_consolidate_ai = QCheckBox("允许模型参与巩固（只产出候选，不改身份）")
        form3.addRow("", self.set_consolidate_ai)
        self.set_consolidate_minutes = QSpinBox()
        self.set_consolidate_minutes.setRange(10, 1440)
        self.set_consolidate_minutes.setSingleStep(10)
        form3.addRow("巩固间隔（分钟）", self.set_consolidate_minutes)
        self.set_consolidate_batch = QSpinBox()
        self.set_consolidate_batch.setRange(5, 120)
        form3.addRow("每次最多整理（条）", self.set_consolidate_batch)
        self.set_share = QCheckBox("把状态和记忆导出给 QQ 桥接")
        form3.addRow("", self.set_share)
        self.set_recall_limit = QSpinBox()
        self.set_recall_limit.setRange(1, 12)
        form3.addRow("QQ 单次最多召回（条）", self.set_recall_limit)
        self.set_recall_chars = QSpinBox()
        self.set_recall_chars.setRange(120, 1200)
        self.set_recall_chars.setSingleStep(30)
        form3.addRow("召回文字上限", self.set_recall_chars)
        box3.addLayout(form3)
        layout.addWidget(panel3)

        panel4, box4 = self._panel()
        form4 = QFormLayout()
        self.set_context = QCheckBox("把身份、记忆和状态注入桌宠聊天 / 语音 / 屏幕观察")
        form4.addRow("", self.set_context)
        self.set_working = QCheckBox("启用工作记忆（记住刚才聊到哪、还没弄明白什么）")
        form4.addRow("", self.set_working)
        self.set_candidate_review = QCheckBox("巩固候选需要我确认后才变成长期记忆")
        form4.addRow("", self.set_candidate_review)
        self.set_group_private = QCheckBox("允许在群聊里召回私聊记忆（不建议打开）")
        form4.addRow("", self.set_group_private)
        self.set_max_context = QSpinBox()
        self.set_max_context.setRange(600, 4000)
        self.set_max_context.setSingleStep(100)
        form4.addRow("上下文长度上限（字）", self.set_max_context)
        self.set_explore_per_day = QSpinBox()
        self.set_explore_per_day.setRange(0, 8)
        form4.addRow("每天自主探索上限", self.set_explore_per_day)
        self.set_explore_sources = QSpinBox()
        self.set_explore_sources.setRange(0, 3)
        form4.addRow("每次探索最多看几个来源", self.set_explore_sources)
        box4.addLayout(form4)
        layout.addWidget(panel4)

        save = QPushButton("保存设置")
        save.setObjectName("primary")
        save.clicked.connect(self._save_settings)
        layout.addWidget(save)
        layout.addStretch(1)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        return page

    # ---------- 数据 ----------

    def _load_settings(self) -> None:
        s = self.service.settings
        self.set_name.setText(str(s.get("name", "")))
        self.set_quiet_start.setText(str(s.get("quiet_start", "")))
        self.set_quiet_end.setText(str(s.get("quiet_end", "")))
        self.set_quiet_screen.setChecked(bool(s.get("quiet_screen")))
        self.set_screen_note.setChecked(bool(s.get("screen_note")))
        self.set_pet_reactions.setChecked(bool(s.get("pet_reactions")))
        self.set_enabled.setChecked(bool(s.get("enabled")))
        self.set_proactive.setChecked(bool(s.get("proactive_enabled")))
        self.set_per_day.setValue(int(s.get("proactive_per_day", 3)))
        self.set_min_minutes.setValue(int(s.get("proactive_min_minutes", 60)))
        self.set_consolidate.setChecked(bool(s.get("consolidate_enabled")))
        self.set_consolidate_ai.setChecked(bool(s.get("consolidate_ai")))
        self.set_consolidate_minutes.setValue(int(s.get("consolidate_minutes", 30)))
        self.set_consolidate_batch.setValue(int(s.get("consolidate_batch", 30)))
        self.set_share.setChecked(bool(s.get("share_with_qq")))
        self.set_recall_limit.setValue(int(s.get("recall_limit", 6)))
        self.set_recall_chars.setValue(int(s.get("recall_chars", 420)))
        self.set_context.setChecked(bool(s.get("context_injection", True)))
        self.set_working.setChecked(bool(s.get("working_memory", True)))
        self.set_candidate_review.setChecked(bool(s.get("candidate_review", True)))
        self.set_group_private.setChecked(bool(s.get("group_private_recall", False)))
        self.set_max_context.setValue(int(s.get("max_context_chars", 1600)))
        self.set_explore_per_day.setValue(int(s.get("explore_per_day", 3)))
        self.set_explore_sources.setValue(int(s.get("explore_sources", 3)))

    def _collect_settings(self) -> dict[str, Any]:
        return {
            "name": self.set_name.text().strip()[:24],
            "quiet_start": self.set_quiet_start.text().strip()[:5],
            "quiet_end": self.set_quiet_end.text().strip()[:5],
            "quiet_screen": self.set_quiet_screen.isChecked(),
            "screen_note": self.set_screen_note.isChecked(),
            "pet_reactions": self.set_pet_reactions.isChecked(),
            "enabled": self.set_enabled.isChecked(),
            "proactive_enabled": self.set_proactive.isChecked(),
            "proactive_per_day": self.set_per_day.value(),
            "proactive_min_minutes": self.set_min_minutes.value(),
            "consolidate_enabled": self.set_consolidate.isChecked(),
            "consolidate_ai": self.set_consolidate_ai.isChecked(),
            "consolidate_minutes": self.set_consolidate_minutes.value(),
            "consolidate_batch": self.set_consolidate_batch.value(),
            "share_with_qq": self.set_share.isChecked(),
            "recall_limit": self.set_recall_limit.value(),
            "recall_chars": self.set_recall_chars.value(),
            "context_injection": self.set_context.isChecked(),
            "working_memory": self.set_working.isChecked(),
            "candidate_review": self.set_candidate_review.isChecked(),
            "group_private_recall": self.set_group_private.isChecked(),
            "max_context_chars": self.set_max_context.value(),
            "explore_per_day": self.set_explore_per_day.value(),
            "explore_sources": self.set_explore_sources.value(),
        }

    def _refresh_overview(self) -> None:
        try:
            snap = self.service.snapshot()
        except Exception as exc:
            self.status.setText(f"读取状态失败：{exc}")
            return
        affect = snap.get("affect") or {}
        state = affect.get("label", "—")
        tone = affect.get("tone", "")
        self.ov_state.setText(
            f"{state}　精力 {affect.get('energy', 0):.2f}　社交 {affect.get('social', 0):.2f}"
            f"　压力 {affect.get('stress', 0):.2f}\n{tone}"
        )
        self.ov_state.setWordWrap(True)
        rhythm = snap.get("rhythm") or {}
        quiet = "（静默时段）" if snap.get("quiet") else ""
        self.ov_rhythm.setText(
            f"已用 {rhythm.get('count', 0)} / {rhythm.get('limit', 0)}，剩余 {rhythm.get('left', 0)}{quiet}"
        )
        memory = snap.get("memory") or {}
        kinds = "、".join(
            f"{KIND_LABELS.get(key, key)} {value}"
            for key, value in (memory.get("by_kind") or {}).items()
        )
        self.ov_memory.setText(
            f"{memory.get('active', 0)} 条活动记忆，{memory.get('episodes', 0)} 条情节"
            + (f"（{kinds}）" if kinds else "")
        )
        working = snap.get("working") or {}
        topic = str(working.get("topic") or "").strip()
        questions = working.get("open_questions") or []
        if topic:
            self.ov_working.setText(
                f"这轮在聊：{topic}" + (f"；还有 {len(questions)} 件没弄明白" if questions else "")
            )
        else:
            self.ov_working.setText("还没有工作记忆，聊两句就会记起来")
        pending = int(memory.get("candidates", 0) or 0)
        needs = int(memory.get("needs_review", 0) or 0)
        self.ov_review.setText(
            f"{pending} 条候选等你确认；{needs} 条需要重新确认"
            if (pending or needs)
            else "没有待审候选，也没有需要重新确认的记忆"
        )
        export = snap.get("export") or {}
        inbox = snap.get("inbox") or {}
        if export.get("exists"):
            age = int(export.get("age_seconds", 0))
            self.ov_export.setText(
                f"已导出，{age} 秒前更新；收件箱 {inbox.get('offset', 0)} 字节已读"
            )
        else:
            self.ov_export.setText("还没有导出，QQ 桥接暂时读不到她的状态")
        audit = self.service.identity.audit_tail(3)
        if audit:
            lines = []
            for row in reversed(audit):
                stamp = time.strftime("%m-%d %H:%M", time.localtime(row.get("at", 0)))
                lines.append(f"{stamp} {row.get('event', '')}")
            self.ov_audit.setText("最近的操作：" + "　".join(lines))

    def _refresh_identity(self) -> None:
        self.core_edit.setPlainText(self.service.identity.core())
        self.growth_edit.setPlainText(self.service.identity.growth())
        for kind, combo in (("core", self.core_history), ("persona", self.persona_history)):
            combo.clear()
            for row in self.service.identity.history(kind, 10):
                stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(row.get("mtime", 0)))
                combo.addItem(f"{stamp} · {row.get('name')}", row.get("name"))

    def _refresh_memory(self) -> None:
        kind = str(self.kind_filter.currentData() or "")
        status = str(self.status_filter.currentData() or "active")
        try:
            rows = self.service.memory_rows(status=status, kind=kind)
        except Exception as exc:
            self.memory_note.setText(f"读取记忆失败：{exc}")
            return
        self.memory_table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            kind_label = KIND_LABELS.get(str(row.get("kind")), str(row.get("kind")))
            if row.get("level"):
                kind_label += f"·{row.get('level')}"
            text = str(row.get("text") or "")
            if row.get("derived_status") == "needs_review":
                text = "⚠ " + text
            weight = row.get("weight", 0)
            cells = [
                kind_label,
                text,
                "—" if status == "candidate" else f"{weight:.2f}",
                f"{row.get('confidence', 0):.2f} {row.get('confidence_label', '')}".strip(),
                str(row.get("source_label") or row.get("source") or ""),
                _when(row.get("last_access") or row.get("created_at") or 0),
            ]
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row.get("id"))
                if column == 1:
                    extra = [part for part in (row.get("why"), row.get("caveat")) if part]
                    if extra:
                        item.setToolTip("\n".join(str(part) for part in extra))
                self.memory_table.setItem(index, column, item)
        if status == "candidate":
            self.memory_note.setText(
                f"共 {len(rows)} 条候选。它们是巩固出来的推断，不会参与召回；"
                "通过后才变成活动记忆，驳回会留在候选历史里。"
            )
        else:
            self.memory_note.setText(
                f"共 {len(rows)} 条。权重会随时间按遗忘曲线下降，被真正用过的记忆会加固；"
                "归档和更正都保留历史，“彻底忘记”才会清掉原文并留下墓碑。"
            )

    # ---------- 动作 ----------

    def _save_settings(self) -> None:
        try:
            self.service.save_settings(self._collect_settings())
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            self.status.setText(f"保存失败：{exc}")
            return
        self._load_settings()
        self._refresh_overview()
        self.status.setText("设置已保存。")

    def _save_core(self) -> None:
        ok, message = self.service.identity.set_core(self.core_edit.toPlainText())
        self._after_identity(ok, message)

    def _save_growth(self) -> None:
        ok, message = self.service.identity.set_growth(self.growth_edit.toPlainText())
        self._after_identity(ok, message)

    def _after_identity(self, ok: bool, message: str) -> None:
        if not ok:
            QMessageBox.warning(self, "没有保存", message)
            self.status.setText(message)
            return
        self.service.export(force=True)
        self._refresh_identity()
        self.status.setText(message)

    def _restore(self, kind: str) -> None:
        combo = self.core_history if kind == "core" else self.persona_history
        name = combo.currentData()
        if not name:
            self.status.setText("还没有可回滚的历史版本。")
            return
        if QMessageBox.question(self, "回滚确认", f"用 {name} 覆盖当前内容？当前版本会先备份。") != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.service.identity.restore(kind, str(name))
        if not ok:
            QMessageBox.warning(self, "回滚失败", message)
            return
        self.service.export(force=True)
        self._refresh_identity()
        self.status.setText(message)

    def _selected_memory(self) -> str:
        rows = self.memory_table.selectionModel().selectedRows() if self.memory_table.selectionModel() else []
        if not rows:
            return ""
        item = self.memory_table.item(rows[0].row(), 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _add_memory(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getMultiLineText(self, "记住一条", "要记住的内容：")
        if not ok or not text.strip():
            return
        kind = str(self.kind_filter.currentData() or "fact") or "fact"
        if kind == "curiosity":
            kind = "fact"
        success, message = self.service.add_memory(text.strip(), kind)
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _correct_memory(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先在表格里选中一条记忆。")
            return
        text, ok = QInputDialog.getMultiLineText(self, "更正记忆", "更正后的说法：")
        if not ok or not text.strip():
            return
        success, message = self.service.correct_memory(item_id, text.strip())
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _archive_memory(self) -> None:
        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先在表格里选中一条记忆。")
            return
        success, message = self.service.archive_memory(item_id)
        self.status.setText(message)
        if success:
            self._refresh_memory()

    def _restore_memory(self) -> None:
        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先在表格里选中一条记忆。")
            return
        success, message = self.service.restore_memory(item_id)
        self.status.setText(message)
        if success:
            self._refresh_memory()

    def _approve_candidate(self) -> None:
        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先切到“待审核候选”，选中一条候选。")
            return
        success, message = self.service.approve_candidate(item_id)
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _reject_candidate(self) -> None:
        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先切到“待审核候选”，选中一条候选。")
            return
        success, message = self.service.reject_candidate(item_id)
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _forget_memory(self) -> None:
        item_id = self._selected_memory()
        if not item_id:
            self.status.setText("先在表格里选中一条记忆。")
            return
        if QMessageBox.question(
            self,
            "彻底忘记",
            "这会清掉原文，只留下不可还原的删除记录，无法从界面恢复。确定吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        success, message = self.service.forget_memory(item_id)
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _backup_now(self) -> None:
        ok, message = self.service.backup_now()
        self.status.setText(message)

    def _restore_backup(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        rows = self.service.backups()
        if not rows:
            self.status.setText("还没有记忆备份。")
            return
        labels = [
            f"{row.get('name')} · {_when(row.get('mtime', 0))}"
            for row in rows
        ]
        choice, ok = QInputDialog.getItem(self, "从备份恢复", "选择一份备份：", labels, 0, False)
        if not ok or not choice:
            return
        index = labels.index(choice)
        name = str(rows[index].get("name") or "")
        if QMessageBox.question(
            self, "恢复备份", f"用 {name} 覆盖当前记忆？当前状态会先备份。"
        ) != QMessageBox.StandardButton.Yes:
            return
        success, message = self.service.restore_backup(name)
        self.status.setText(message)
        if success:
            self._refresh_memory()
            self._refresh_overview()

    def _integrity_check(self) -> None:
        report = self.service.integrity_report()
        lines = [
            f"活动条目：{report.get('items', 0)}，情节：{report.get('episodes', 0)}",
            f"待审候选：{report.get('pending_candidates', 0)}，墓碑：{report.get('tombstones', 0)}",
            f"重复 ID：{len(report.get('duplicates') or [])}",
            f"缺少主体：{len(report.get('missing_subject') or [])}",
            f"非法可见域：{len(report.get('invalid_visibility') or [])}",
            f"孤立证据：{len(report.get('orphan_evidence') or [])}",
        ]
        title = "体检通过" if report.get("ok") else "发现需要处理的问题"
        QMessageBox.information(self, title, "\n".join(lines))
        self.status.setText(title)

    def _consolidate(self) -> None:
        ok, message = self.service.consolidate_now()
        self.status.setText(message)
        self._refresh_overview()

    def _reset_affect(self) -> None:
        if QMessageBox.question(self, "重置状态", "把当前的功能状态恢复到基线？") != QMessageBox.StandardButton.Yes:
            return
        self.service.reset_affect()
        self._refresh_overview()
        self.status.setText("状态已重置。")

    def _export_now(self) -> None:
        self.service.export(force=True)
        self._refresh_overview()
        self.status.setText("已导出给 QQ 桥接。")


def _when(ts: float) -> str:
    if not ts:
        return "—"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))
