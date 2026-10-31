from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from ai_client import AIRequest, PROVIDER_LABELS
from config import Config
from system_utils import is_startup_enabled, set_startup_enabled
from theme import ACCENT, BORDER, CARD, DIALOG_STYLE, MUTED, TEXT
from ui_widgets import DraggableHeader
from version import APP_VERSION


class ThemedCheckBox(QCheckBox):
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(255, 255, 255), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
        painter.drawLine(
            QPointF(x + w * 0.24, y + h * 0.52),
            QPointF(x + w * 0.43, y + h * 0.71),
        )
        painter.drawLine(
            QPointF(x + w * 0.43, y + h * 0.71),
            QPointF(x + w * 0.78, y + h * 0.30),
        )


class SettingsDialog(QDialog):
    settings_saved = Signal()
    clear_chat_requested = Signal()
    observe_now_requested = Signal()
    plugins_requested = Signal()
    check_updates_requested = Signal()

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._test_request: AIRequest | None = None
        self.setWindowTitle("LFELKEEA · 设置")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.resize(540, 700)
        self.setMinimumSize(520, 600)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(0)
        card = QFrame()
        card.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(42)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(0, 0, 0, 200))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        header = DraggableHeader(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("设置")
        title.setObjectName("title")
        subtitle = QLabel("LFELKEEA 的 AI 与桌面行为")
        subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        close_btn = QPushButton("×")
        close_btn.setObjectName("ghost")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ font-size: 17px; color: {MUTED}; background: transparent; border: none; }}"
            f"QPushButton:hover {{ color: #ffffff; background: {ACCENT}; border-radius: 6px; }}"
        )
        close_btn.clicked.connect(self.reject)
        header_layout.addLayout(title_box, 1)
        header_layout.addWidget(close_btn)
        layout.addWidget(header)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background-color: {BORDER}; border: none;")
        layout.addWidget(divider)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 2, 0)
        body.setSpacing(12)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)

        section_ai = QLabel("AI 对话")
        section_ai.setObjectName("section")
        body.addWidget(section_ai)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(9)
        grid.setColumnStretch(1, 1)

        self.provider = QComboBox()
        for value, label in (
            ("pollinations", "免费公共通道（无需密钥）"),
            ("openai", "OpenAI 兼容接口"),
            ("ollama", "本地 Ollama"),
        ):
            self.provider.addItem(label, value)
        self.provider.currentIndexChanged.connect(self._update_fields)
        self._add_row(grid, 0, "对话通道", self.provider)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("openai-fast")
        self._add_row(grid, 1, "模型", self.model_edit)

        self.base_edit = QLineEdit()
        self.base_edit.setPlaceholderText("https://api.openai.com/v1")
        self.base_label, self.base_row_widget = self._add_row(grid, 2, "接口地址", self.base_edit)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.key_label, self.key_row_widget = self._add_row(grid, 3, "API Key", self.key_edit)

        body.addLayout(grid)

        self.note = QLabel("")
        self.note.setObjectName("muted")
        self.note.setWordWrap(True)
        body.addWidget(self.note)

        test_row = QHBoxLayout()
        test_row.setSpacing(10)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.test_btn.clicked.connect(self._test_connection)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        self.test_status.setObjectName("muted")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status, 1)
        body.addLayout(test_row)

        section_behavior = QLabel("桌面行为")
        section_behavior.setObjectName("section")
        body.addWidget(section_behavior)

        chatter_row = QHBoxLayout()
        chatter_row.setSpacing(10)
        chatter_label = QLabel("主动搭话")
        chatter_label.setObjectName("muted")
        chatter_label.setFixedWidth(70)
        self.chatter_frequency = QComboBox()
        for value, label in (
            ("off", "关闭，不主动搭话"),
            ("rare", "很少，约 5–10 分钟一次"),
            ("normal", "标准，约 1.5–3 分钟一次"),
            ("often", "频繁，约 45–90 秒一次"),
        ):
            self.chatter_frequency.addItem(label, value)
        chatter_row.addWidget(chatter_label)
        chatter_row.addWidget(self.chatter_frequency, 1)
        body.addLayout(chatter_row)

        self.top_check = ThemedCheckBox("始终置顶显示")
        self.startup_check = ThemedCheckBox("开机自动启动")
        body.addWidget(self.top_check)
        body.addWidget(self.startup_check)

        section_screen = QLabel("屏幕观察")
        section_screen.setObjectName("section")
        body.addWidget(section_screen)

        self.screen_enable = ThemedCheckBox("允许定时观察屏幕，并主动和主人说话")
        body.addWidget(self.screen_enable)

        screen_grid = QGridLayout()
        screen_grid.setContentsMargins(0, 0, 0, 0)
        screen_grid.setHorizontalSpacing(12)
        screen_grid.setVerticalSpacing(9)
        screen_grid.setColumnStretch(1, 1)

        self.screen_interval = QComboBox()
        for seconds, label in (
            (60, "每 1 分钟"),
            (120, "每 2 分钟"),
            (300, "每 5 分钟"),
            (600, "每 10 分钟"),
        ):
            self.screen_interval.addItem(label, seconds)
        self._add_row(screen_grid, 0, "观察间隔", self.screen_interval)

        self.screen_mode = QComboBox()
        for value, label in (
            ("auto", "自动（推荐）"),
            ("window", "只看窗口标题，不上传截图"),
            ("vision", "画面理解（上传压缩截图）"),
            ("ocr", "屏幕文字识别（本地 OCR）"),
        ):
            self.screen_mode.addItem(label, value)
        self._add_row(screen_grid, 1, "观察方式", self.screen_mode)
        body.addLayout(screen_grid)

        self.screen_fullscreen = ThemedCheckBox("全屏应用或游戏时暂停")
        self.screen_safe = ThemedCheckBox("检测到密码、支付等敏感窗口时暂停")
        body.addWidget(self.screen_fullscreen)
        body.addWidget(self.screen_safe)

        self.screen_note = QLabel(
            "默认只读取前台窗口标题，不截屏、不上传。选择“画面理解”或“屏幕文字识别”"
            "时才会截取主屏幕；截图只在内存中压缩，不会写入磁盘。"
        )
        self.screen_note.setObjectName("muted")
        self.screen_note.setWordWrap(True)
        body.addWidget(self.screen_note)

        screen_actions = QHBoxLayout()
        screen_actions.setSpacing(8)
        self.observe_btn = QPushButton("立即看一眼")
        self.observe_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.observe_btn.clicked.connect(self.observe_now_requested.emit)
        self.screen_status = QLabel("屏幕观察未开启")
        self.screen_status.setObjectName("muted")
        self.screen_status.setWordWrap(True)
        screen_actions.addWidget(self.observe_btn)
        screen_actions.addWidget(self.screen_status, 1)
        body.addLayout(screen_actions)

        section_plugins = QLabel("扩展与插件")
        section_plugins.setObjectName("section")
        body.addWidget(section_plugins)

        plugin_row = QHBoxLayout()
        plugin_row.setSpacing(8)
        plugin_hint = QLabel("插件默认不运行，需要在管理界面手动启用。")
        plugin_hint.setObjectName("muted")
        plugin_hint.setWordWrap(True)
        self.plugin_btn = QPushButton("管理插件…")
        self.plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.plugin_btn.clicked.connect(self.plugins_requested.emit)
        plugin_row.addWidget(plugin_hint, 1)
        plugin_row.addWidget(self.plugin_btn)
        body.addLayout(plugin_row)

        section_update = QLabel("软件更新")
        section_update.setObjectName("section")
        body.addWidget(section_update)

        update_row = QHBoxLayout()
        update_row.setSpacing(10)
        self.version_label = QLabel(f"当前版本 v{APP_VERSION}")
        self.version_label.setObjectName("muted")
        self.update_btn = QPushButton("检查更新")
        self.update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_btn.clicked.connect(self.check_updates_requested.emit)
        update_row.addWidget(self.version_label, 1)
        update_row.addWidget(self.update_btn)
        body.addLayout(update_row)

        self.update_auto_check = ThemedCheckBox("启动时自动检查更新")
        body.addWidget(self.update_auto_check)

        self.update_status = QLabel("")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        body.addWidget(self.update_status)

        body.addStretch(1)

        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        clear_btn = QPushButton("清空对话")
        clear_btn.setObjectName("ghost")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_chat)
        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setObjectName("primary")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._save)
        bottom.addWidget(clear_btn)
        bottom.addStretch(1)
        bottom.addWidget(cancel_btn)
        bottom.addWidget(save_btn)
        layout.addLayout(bottom)

        self._center_on_screen()

    def _add_row(self, grid: QGridLayout, row: int, label_text: str, field: QWidget) -> tuple[QLabel, QWidget]:
        label = QLabel(label_text)
        label.setObjectName("muted")
        label.setFixedWidth(70)
        grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(field, row, 1)
        return label, field

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.move(
            area.center().x() - self.width() // 2,
            area.center().y() - self.height() // 2,
        )

    def _load_values(self) -> None:
        ai = self.config.ai
        index = self.provider.findData(ai.get("provider", "pollinations"))
        self.provider.setCurrentIndex(max(0, index))
        self.model_edit.setText(str(ai.get("model", "")))
        self.base_edit.setText(str(ai.get("base_url", "")))
        self.key_edit.setText(str(ai.get("api_key", "")))
        level = str(self.config.get("idle_chatter_frequency", "normal"))
        if not self.config.get("idle_chatter", True):
            level = "off"
        index = self.chatter_frequency.findData(level)
        self.chatter_frequency.setCurrentIndex(max(0, index))
        self.top_check.setChecked(bool(self.config.get("always_on_top", True)))
        self.startup_check.setChecked(is_startup_enabled())
        screen = self.config.get("screen_watch", {})
        if not isinstance(screen, dict):
            screen = {}
        self.screen_enable.setChecked(bool(screen.get("enabled", False)))
        index = self.screen_interval.findData(int(screen.get("interval_seconds", 120)))
        self.screen_interval.setCurrentIndex(max(0, index))
        mode = str(screen.get("mode", "auto"))
        index = self.screen_mode.findData(mode)
        self.screen_mode.setCurrentIndex(max(0, index))
        self.screen_fullscreen.setChecked(bool(screen.get("pause_when_fullscreen", True)))
        self.screen_safe.setChecked(bool(screen.get("pause_sensitive", True)))
        updates = self.config.get("updates", {})
        if not isinstance(updates, dict):
            updates = {}
        self.update_auto_check.setChecked(bool(updates.get("auto_check", True)))
        self._update_fields()

    def _update_fields(self) -> None:
        provider = self.provider.currentData()
        if provider == "pollinations":
            self.base_label.setVisible(False)
            self.base_edit.setVisible(False)
            self.key_label.setVisible(False)
            self.key_edit.setVisible(False)
            self.model_edit.setPlaceholderText("openai-fast")
            self.note.setText(
                "免费公共通道无需 API Key，开箱即用。对话内容会发送到 "
                "Pollinations 公共接口，请勿输入敏感信息。"
            )
        elif provider == "ollama":
            self.base_label.setVisible(True)
            self.base_edit.setVisible(True)
            self.key_label.setVisible(False)
            self.key_edit.setVisible(False)
            self.base_edit.setPlaceholderText("http://127.0.0.1:11434/v1")
            self.model_edit.setPlaceholderText("qwen2.5:7b")
            self.note.setText(
                "先在本机启动 Ollama，再填写模型名称。对话只在本机处理。"
            )
        else:
            self.base_label.setVisible(True)
            self.base_edit.setVisible(True)
            self.key_label.setVisible(True)
            self.key_edit.setVisible(True)
            self.base_edit.setPlaceholderText("https://api.openai.com/v1")
            self.model_edit.setPlaceholderText("gpt-4o-mini")
            self.note.setText(
                "支持 OpenAI、DeepSeek、SiliconFlow 等兼容接口。"
                "密钥只保存在本机 data/config.json 中，不会上传到别处。"
            )

    def _current_ai_config(self) -> dict:
        return {
            "provider": self.provider.currentData(),
            "model": self.model_edit.text().strip(),
            "base_url": self.base_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "temperature": 0.85,
            "timeout": 25,
        }

    def _test_connection(self) -> None:
        if self._test_request is not None:
            return
        self.test_btn.setEnabled(False)
        self.test_status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.test_status.setText("连接中，可能要点时间…")
        messages = [{"role": "user", "content": "只回复四个字：连接成功"}]
        request = AIRequest(self._current_ai_config(), messages)
        self._test_request = request
        request.signals.ok.connect(self._test_ok)
        request.signals.fail.connect(self._test_fail)
        request.start()

    def _test_ok(self, reply: str) -> None:
        self._test_request = None
        self.test_btn.setEnabled(True)
        self.test_status.setStyleSheet("color: #8ed6a2; font-size: 11px;")
        text = reply if len(reply) <= 40 else reply[:40] + "…"
        self.test_status.setText(f"连接成功：{text}")

    def _test_fail(self, error: str) -> None:
        self._test_request = None
        self.test_btn.setEnabled(True)
        self.test_status.setStyleSheet("color: #e28189; font-size: 11px;")
        text = error if len(error) <= 110 else error[:110] + "…"
        self.test_status.setText(f"连接失败：{text}")

    def _clear_chat(self) -> None:
        self.clear_chat_requested.emit()
        self.test_status.setStyleSheet("color: #8ed6a2; font-size: 11px;")
        self.test_status.setText("对话已清空。")

    def _save(self) -> None:
        ai = self.config.ai
        ai["provider"] = self.provider.currentData()
        ai["model"] = self.model_edit.text().strip() or ("openai-fast" if ai["provider"] == "pollinations" else "")
        ai["base_url"] = self.base_edit.text().strip()
        ai["api_key"] = self.key_edit.text().strip()
        ai["temperature"] = 0.85
        chatter_level = str(self.chatter_frequency.currentData() or "normal")
        self.config.set("idle_chatter", chatter_level != "off", save=False)
        self.config.set("idle_chatter_frequency", chatter_level, save=False)
        self.config.set("always_on_top", self.top_check.isChecked(), save=False)
        screen = self.config.get("screen_watch", {})
        if not isinstance(screen, dict):
            screen = {}
        screen.update(
            {
                "enabled": self.screen_enable.isChecked(),
                "interval_seconds": int(self.screen_interval.currentData() or 120),
                "mode": str(self.screen_mode.currentData() or "auto"),
                "pause_when_fullscreen": self.screen_fullscreen.isChecked(),
                "pause_sensitive": self.screen_safe.isChecked(),
            }
        )
        self.config.set("screen_watch", screen, save=False)
        updates = self.config.get("updates", {})
        if not isinstance(updates, dict):
            updates = {}
        updates["auto_check"] = self.update_auto_check.isChecked()
        self.config.set("updates", updates, save=False)
        set_startup_enabled(self.startup_check.isChecked())
        self.config.save()
        self.settings_saved.emit()
        self.accept()

    def set_screen_status(self, text: str) -> None:
        self.screen_status.setText(text or "屏幕观察未开启")

    def set_update_status(self, text: str) -> None:
        self.update_status.setText(str(text or ""))

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)
