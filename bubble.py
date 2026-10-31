from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from theme import BUBBLE_BG, BUBBLE_BORDER, FONT_FAMILY, TEXT


class SpeechBubble(QWidget):
    MAX_TEXT_WIDTH = 290
    TAIL = 11

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("LFELKEEA 的对话")
        self._text = ""
        self._name_font = QFont(FONT_FAMILY, 9, QFont.Weight.DemiBold)
        self._font = QFont(FONT_FAMILY, 10)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)
        self._fading_out = False

    def show_text(self, text: str, anchor_rect: QRect, duration: int = 5200) -> None:
        text = (text or "").strip()
        if not text:
            return
        if len(text) > 150:
            text = text[:150].rstrip() + "……"
        self._text = text

        metrics = QFontMetrics(self._font)
        rect = metrics.boundingRect(
            QRect(0, 0, self.MAX_TEXT_WIDTH, 1200),
            Qt.TextFlag.TextWordWrap,
            text,
        )
        width = max(104, min(self.MAX_TEXT_WIDTH, rect.width()) + 30)
        text_height = metrics.boundingRect(
            QRect(0, 0, width - 30, 1200), Qt.TextFlag.TextWordWrap, text
        ).height()
        height = text_height + 58 + self.TAIL
        screen = QGuiApplication.screenAt(anchor_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()

        x = anchor_rect.center().x() - width // 2
        y = anchor_rect.top() - height + 4
        if y < area.top() + 4:
            y = min(anchor_rect.bottom() - 8, area.bottom() - height)
        x = max(area.left() + 4, min(x, area.right() - width - 4))
        y = max(area.top() + 4, min(y, area.bottom() - height - 4))
        self.setGeometry(x, y, width, height)
        self._fading_out = False
        self._fade.stop()
        self.setWindowOpacity(0.0)
        self.show()
        self.raise_()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._timer.start(duration)

    def hide_now(self) -> None:
        self._fading_out = False
        self._timer.stop()
        self._fade.stop()
        self.hide()

    def _fade_out(self) -> None:
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - self.TAIL - 2.0)

        path = QPainterPath()
        path.addRoundedRect(box, 13, 13)
        cx = box.left() + box.width() * 0.5
        tail = QPainterPath()
        tail.moveTo(cx - 8, box.bottom() - 1)
        tail.lineTo(cx + 8, box.bottom() - 1)
        tail.lineTo(cx, box.bottom() + self.TAIL - 1)
        tail.closeSubpath()
        path = path.united(tail)

        painter.setPen(QPen(BUBBLE_BORDER, 1.4))
        painter.setBrush(BUBBLE_BG)
        painter.drawPath(path)

        painter.setFont(self._name_font)
        painter.setPen(QColor("#e96872"))
        painter.drawText(
            QRectF(14, 8, box.width() - 28, 18),
            Qt.AlignmentFlag.AlignLeft,
            "LFELKEEA",
        )

        painter.setFont(self._font)
        painter.setPen(QColor(TEXT))
        text_rect = QRectF(14, 29, box.width() - 28, box.height() - 38)
        painter.drawText(
            text_rect,
            int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft),
            self._text,
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802
        event.ignore()
