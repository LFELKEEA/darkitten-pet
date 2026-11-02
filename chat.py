from __future__ import annotations

import random
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPixmap,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_client import AIRequest, AIError, PROVIDER_LABELS, SYSTEM_PROMPT
from app_paths import ASSET_DIR
from config import Config
from conversation import Conversation
from local_replies import AI_FAIL_NOTES, local_reply
from theme import ACCENT, BORDER, CARD, MUTED, TEXT


def circular_pixmap(pixmap: QPixmap, size: int) -> QPixmap:
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    painter.setClipPath(path)
    scaled = pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = (scaled.width() - size) // 2
    y = (scaled.height() - size) // 2
    painter.drawPixmap(-x, -y, scaled)
    painter.end()
    return result


class MessageBubble(QWidget):
    def __init__(self, role: str, text: str, avatar: QPixmap | None = None) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.label.setFont(QFont("Microsoft YaHei UI", 10))
        metrics = QFontMetrics(self.label.font())
        lines = text.splitlines() or [text]
        natural = max((metrics.horizontalAdvance(line) for line in lines), default=0)
        self.label.setFixedWidth(min(300, max(86, natural + 44)))
        self.label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)

        if role == "pet":
            if avatar is not None and not avatar.isNull():
                avatar_label = QLabel()
                avatar_label.setFixedSize(30, 30)
                avatar_label.setPixmap(circular_pixmap(avatar, 30))
                avatar_label.setAlignment(Qt.AlignmentFlag.AlignBottom)
                layout.addWidget(avatar_label, 0, Qt.AlignmentFlag.AlignTop)
            self.label.setStyleSheet(
                f"""
                QLabel {{
                    background-color: #2c2025;
                    color: {TEXT};
                    border: 1px solid #3d2a30;
                    border-top-left-radius: 4px;
                    border-top-right-radius: 13px;
                    border-bottom-left-radius: 13px;
                    border-bottom-right-radius: 13px;
                    padding: 9px 12px;
                }}
                """
            )
            layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignTop)
            layout.addStretch(1)
        else:
            self.label.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {ACCENT};
                    color: #ffffff;
                    border-top-left-radius: 13px;
                    border-top-right-radius: 4px;
                    border-bottom-left-radius: 13px;
                    border-bottom-right-radius: 13px;
                    padding: 9px 12px;
                }}
                """
            )
            layout.addStretch(1)
            layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignTop)


class TypingBubble(QWidget):
    def __init__(self, avatar: QPixmap | None = None) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if avatar is not None and not avatar.isNull():
            avatar_label = QLabel()
            avatar_label.setFixedSize(30, 30)
            avatar_label.setPixmap(circular_pixmap(avatar, 30))
            layout.addWidget(avatar_label, 0, Qt.AlignmentFlag.AlignTop)
        self.label = QLabel("LFELKEEA 正在想")
        self.label.setStyleSheet(
            f"""
            QLabel {{
                background-color: #2c2025;
                color: {MUTED};
                border: 1px solid #3d2a30;
                border-radius: 12px;
                padding: 9px 12px;
            }}
            """
        )
        layout.addWidget(self.label, 0, Qt.AlignmentFlag.AlignTop)
        layout.addStretch(1)
        self._dots = 0
        self._timer = QTimer(self)
        self._timer.setInterval(380)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def _animate(self) -> None:
        self._dots = (self._dots + 1) % 3
        self.label.setText("LFELKEEA 正在想" + "." * self._dots)

    def stop(self) -> None:
        self._timer.stop()


class ChatInput(QTextEdit):
    send_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setPlaceholderText("和 LFELKEEA 说点什么……")
        self.setFixedHeight(62)
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: #1a1316;
                color: {TEXT};
                border: 1px solid {BORDER};
                border-radius: 10px;
                padding: 8px 10px;
                selection-background-color: {ACCENT};
            }}
            QTextEdit:focus {{
                border: 1px solid #7a3a44;
            }}
            """
        )

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
            return
        super().keyPressEvent(event)


class ChatWindow(QWidget):
    reply_ready = Signal(str)
    user_sent = Signal(str)
    settings_requested = Signal()
    visibility_changed = Signal(bool)

    def __init__(
        self,
        config: Config,
        conversation: Conversation | None = None,
        context_provider: Callable[..., str] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.config = config
        self.context_provider = context_provider
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, False)
        self.setWindowTitle("和 LFELKEEA 聊天")
        self.resize(452, 636)
        self.setMinimumSize(452, 636)
        self.setMaximumSize(452, 636)

        self._busy = False
        self._active_request: AIRequest | None = None
        self._request_token = 0
        self._typing: TypingBubble | None = None
        self.conversation = conversation or Conversation(24, self)
        self._drag_origin: QPoint | None = None
        self._window_origin: QPoint | None = None
        self._user_moved = False
        self._avatar = QPixmap(str(ASSET_DIR / "avatar.png")) if (ASSET_DIR / "avatar.png").exists() else QPixmap()
        self._fail_notice_shown = False

        self._build_ui()
        self._add_system_note(f"AI 通道：{self._provider_text()}")
        self.append_message("pet", "主人，找我有什么事？")
        for item in self.conversation.messages(8):
            role = item.get("role")
            if role in ("user", "assistant"):
                self.append_message("user" if role == "user" else "pet", item.get("content", ""))

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("chatCard")
        self.card.setStyleSheet(
            f"""
            QFrame#chatCard {{
                background-color: {CARD};
                border: 1px solid {BORDER};
                border-radius: 18px;
            }}
            """
        )
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("chatHeader")
        header.setFixedHeight(74)
        header.setStyleSheet(
            f"""
            QWidget#chatHeader {{
                background-color: transparent;
                border-bottom: 1px solid #332329;
            }}
            """
        )
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 12, 12, 10)
        header_layout.setSpacing(10)

        avatar = QLabel()
        avatar.setFixedSize(44, 44)
        if not self._avatar.isNull():
            avatar.setPixmap(circular_pixmap(self._avatar, 44))
        avatar.setStyleSheet(f"border: 1px solid {ACCENT}; border-radius: 22px;")

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 2, 0, 2)
        text_box.setSpacing(1)
        name = QLabel("LFELKEEA")
        name.setStyleSheet(f"color: {TEXT}; font-size: 15px; font-weight: 600; background: transparent;")
        status = QLabel("桌面陪伴中")
        status.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        text_box.addWidget(name)
        text_box.addWidget(status)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.setFixedHeight(26)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self.settings_requested.emit)

        minimize_btn = QPushButton("—")
        minimize_btn.setObjectName("ghost")
        minimize_btn.setFixedSize(26, 26)
        minimize_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        minimize_btn.clicked.connect(self.hide)

        close_btn = QPushButton("×")
        close_btn.setObjectName("ghost")
        close_btn.setFixedSize(26, 26)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton#ghost {{ color: {MUTED}; font-size: 16px; }}"
            f"QPushButton#ghost:hover {{ color: #ffffff; background: {ACCENT}; }}"
        )
        close_btn.clicked.connect(self.hide)

        header_layout.addWidget(avatar)
        header_layout.addLayout(text_box, 1)
        header_layout.addWidget(self.settings_btn)
        header_layout.addWidget(minimize_btn)
        header_layout.addWidget(close_btn)
        card_layout.addWidget(header)

        self.header = header
        for widget in (header, avatar, name, status):
            widget.installEventFilter(self)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")

        self.messages_host = QWidget()
        self.messages_host.setStyleSheet("background: transparent;")
        self.messages_layout = QVBoxLayout(self.messages_host)
        self.messages_layout.setContentsMargins(16, 14, 16, 10)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages_host)
        card_layout.addWidget(self.scroll, 1)

        chips = QWidget()
        chip_layout = QHBoxLayout(chips)
        chip_layout.setContentsMargins(16, 0, 16, 8)
        chip_layout.setSpacing(6)
        for text in ("今天有点累", "陪我聊聊", "你在做什么"):
            chip = QPushButton(text)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setStyleSheet(
                f"""
                QPushButton {{
                    background: transparent;
                    color: {MUTED};
                    border: 1px solid {BORDER};
                    border-radius: 11px;
                    padding: 4px 9px;
                    font-family: "Microsoft YaHei UI";
                    font-size: 10px;
                }}
                QPushButton:hover {{
                    color: {TEXT};
                    border-color: #6a3b44;
                    background: #2a1e23;
                }}
                """
            )
            chip.clicked.connect(lambda checked=False, t=text: self.send(t))
            chip_layout.addWidget(chip)
        chip_layout.addStretch(1)
        card_layout.addWidget(chips)

        input_row = QWidget()
        input_layout = QHBoxLayout(input_row)
        input_layout.setContentsMargins(16, 0, 16, 8)
        input_layout.setSpacing(8)
        self.input = ChatInput()
        self.input.send_requested.connect(self.send_current)
        self.input.setStyleSheet(
            self.input.styleSheet()
            + f"""
            QTextEdit {{
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
            }}
            """
        )
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("primary")
        self.send_btn.setFixedSize(64, 42)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.clicked.connect(self.send_current)
        self.send_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {ACCENT};
                color: white;
                border: 1px solid #d94c56;
                border-radius: 10px;
                font-family: "Microsoft YaHei UI";
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: #d43b45; }}
            QPushButton:disabled {{ background-color: #4a2a30; color: #9a8187; border-color: #4a2a30; }}
            """
        )
        input_layout.addWidget(self.input, 1)
        input_layout.addWidget(self.send_btn)
        card_layout.addWidget(input_row)

        footer = QLabel("Enter 发送 · Shift+Enter 换行")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet(f"color: #6f5b61; font-size: 9px; padding-bottom: 9px; background: transparent;")
        card_layout.addWidget(footer)

    def _provider_text(self) -> str:
        ai = self.config.ai
        label = PROVIDER_LABELS.get(ai.get("provider", ""), "AI")
        model = (ai.get("model") or "").strip()
        return f"{label} · {model}" if model else label

    def refresh_provider(self) -> None:
        self._add_system_note(f"AI 通道已更新：{self._provider_text()}")

    def _add_system_note(self, text: str) -> None:
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet(
            f"color: #7d676e; font-size: 9px; background: transparent; padding: 2px 6px;"
        )
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, label)
        QTimer.singleShot(40, self._scroll_to_bottom)

    def append_message(self, role: str, text: str) -> None:
        bubble = MessageBubble(role, text, self._avatar if role == "pet" else None)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        QTimer.singleShot(40, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _show_typing(self) -> None:
        self._remove_typing()
        self._typing = TypingBubble(self._avatar)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, self._typing)
        QTimer.singleShot(40, self._scroll_to_bottom)

    def _remove_typing(self) -> None:
        if self._typing is not None:
            self._typing.stop()
            self._typing.setParent(None)
            self._typing.deleteLater()
            self._typing = None

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.send_btn.setDisabled(busy)
        self.send_btn.setText("等等" if busy else "发送")

    def _system_prompt(self, query: str) -> str:
        if self.context_provider is None:
            return SYSTEM_PROMPT
        try:
            block = self.context_provider(
                query=query, viewer="owner", source="pet", channel="private"
            )
        except Exception:
            return SYSTEM_PROMPT
        block = str(block or "").strip()
        if not block:
            return SYSTEM_PROMPT
        return SYSTEM_PROMPT.rstrip() + "\n\n" + block

    def send_current(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.send(text)

    def send(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._busy:
            self._add_system_note("等 LFELKEEA 把上一句说完。")
            return

        self._set_busy(True)
        self.append_message("user", text)
        self.conversation.append("user", text)
        self.user_sent.emit(text)
        self._show_typing()

        system = self._system_prompt(text)
        messages = [{"role": "system", "content": system}] + self.conversation.messages(16)
        ai_config = dict(self.config.ai)
        request = AIRequest(ai_config, messages)
        self._active_request = request
        self._request_token += 1
        token = self._request_token
        request.signals.ok.connect(lambda reply, t=token: self._on_ai_ok(reply, t))
        request.signals.fail.connect(lambda error, t=token: self._on_ai_fail(error, t))
        request.start()

    def _on_ai_ok(self, reply: str, token: int) -> None:
        if token != self._request_token:
            return
        self._remove_typing()
        self._set_busy(False)
        self._active_request = None
        self.conversation.append("assistant", reply)
        self.append_message("pet", reply)
        self.reply_ready.emit(reply)

    def _on_ai_fail(self, error: str, token: int) -> None:
        if token != self._request_token:
            return
        self._remove_typing()
        self._active_request = None
        if not self._fail_notice_shown:
            short = error if len(error) <= 120 else error[:120] + "…"
            self._add_system_note(f"AI 通道没连上（{short}），已切换本地回复。")
            self._fail_notice_shown = True
        reply = local_reply(self.conversation.last_user())
        QTimer.singleShot(350, lambda: self._finish_local(reply, token))

    def _finish_local(self, reply: str, token: int) -> None:
        if token != self._request_token:
            return
        self.conversation.append("assistant", reply)
        self.append_message("pet", reply)
        self.reply_ready.emit(reply)
        self._set_busy(False)

    def clear_history(self) -> None:
        self._request_token += 1
        if self._active_request is not None:
            self._active_request.cancel()
            self._active_request = None
        self._remove_typing()
        self._set_busy(False)
        self.conversation.clear()
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._add_system_note("对话已清空。")
        self.append_message("pet", "嗯，重新开始吧。")

    def show_near(self, pet_rect: QRect) -> None:
        if not self._user_moved:
            screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
            area = screen.availableGeometry()
            x = pet_rect.left() - self.width() - 4
            if x < area.left() + 8:
                x = pet_rect.right() + 4
            if x + self.width() > area.right() - 8:
                x = area.right() - self.width() - 8
            y = pet_rect.center().y() - self.height() // 2
            y = max(area.top() + 8, min(y, area.bottom() - self.height() - 8))
            self.move(x, y)
        else:
            area = (QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()).availableGeometry()
            x = max(area.left() + 4, min(self.x(), area.right() - self.width() - 4))
            y = max(area.top() + 4, min(self.y(), area.bottom() - self.height() - 4))
            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.visibility_changed.emit(False)
        super().hideEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        self.visibility_changed.emit(True)
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        header = getattr(self, "header", None)
        is_header_part = watched is header or (
            header is not None
            and watched in header.findChildren(QWidget)
            and not isinstance(watched, QPushButton)
        )
        if is_header_part:
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_origin = event.globalPosition().toPoint()
                self._window_origin = self.pos()
                return False
            if event.type() == QEvent.Type.MouseMove and self._drag_origin is not None:
                delta = event.globalPosition().toPoint() - self._drag_origin
                if delta.manhattanLength() > 3:
                    self._user_moved = True
                    self.move(self._window_origin + delta)
                return False
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._drag_origin = None
                self._window_origin = None
        return super().eventFilter(watched, event)
