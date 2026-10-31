from __future__ import annotations

import math
import random
from dataclasses import dataclass

from PySide6.QtCore import QElapsedTimer, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from theme import FONT_FAMILY


@dataclass
class Particle:
    x: float
    y: float
    vx: float
    vy: float
    life: float
    ttl: float
    size: float
    color: QColor
    phase: float
    kind: str = "heart"


def heart_path(size: float) -> QPainterPath:
    path = QPainterPath()
    s = size
    path.moveTo(0, s * 0.32)
    path.cubicTo(-s * 0.62, -s * 0.28, -s * 0.34, -s * 0.82, 0, -s * 0.36)
    path.cubicTo(s * 0.34, -s * 0.82, s * 0.62, -s * 0.28, 0, s * 0.32)
    path.closeSubpath()
    return path


class EffectOverlay(QWidget):
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
        self.setWindowTitle("LFELKEEA 的特效")
        self._particles: list[Particle] = []
        self._sleeping = False
        self._pet_rect = QRect()
        self._clock = QElapsedTimer()
        self._clock.start()
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._tick)
        self._heart = heart_path(1.0)

    def follow(self, pet_rect: QRect) -> None:
        screen = QGuiApplication.screenAt(pet_rect.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        margin_x = 110
        top = 150
        bottom = 70
        x = max(area.left(), pet_rect.left() - margin_x)
        y = max(area.top(), pet_rect.top() - top)
        right = min(area.right() + 1, pet_rect.right() + margin_x)
        bottom_y = min(area.bottom() + 1, pet_rect.bottom() + bottom)
        rect = QRect(x, y, max(1, right - x), max(1, bottom_y - y))
        self.setGeometry(rect)
        self._pet_rect = pet_rect.translated(-rect.topLeft())
        if self._particles or self._sleeping:
            self._ensure_visible()

    def emit_hearts(self, origin: QPointF | None = None, count: int = 6) -> None:
        if origin is None:
            origin = QPointF(self.width() * 0.5, self.height() * 0.36)
        colors = [QColor(232, 76, 92), QColor(255, 126, 139), QColor(178, 37, 50), QColor(255, 176, 182)]
        for _ in range(count):
            self._particles.append(
                Particle(
                    x=origin.x() + random.uniform(-24, 24),
                    y=origin.y() + random.uniform(-12, 12),
                    vx=random.uniform(-26, 26),
                    vy=random.uniform(-70, -38),
                    life=0.0,
                    ttl=random.uniform(0.9, 1.5),
                    size=random.uniform(9, 17),
                    color=random.choice(colors),
                    phase=random.uniform(0, math.tau),
                )
            )
        self._restart()

    def emit_sparkles(self, count: int = 8) -> None:
        origin = QPointF(self.width() * 0.5, self.height() * 0.5)
        for _ in range(count):
            self._particles.append(
                Particle(
                    x=origin.x() + random.uniform(-70, 70),
                    y=origin.y() + random.uniform(-90, 90),
                    vx=random.uniform(-18, 18),
                    vy=random.uniform(-32, -8),
                    life=0.0,
                    ttl=random.uniform(0.5, 0.9),
                    size=random.uniform(3, 6),
                    color=QColor(255, 220, 224),
                    phase=random.uniform(0, math.tau),
                    kind="spark",
                )
            )
        self._restart()

    def set_sleeping(self, sleeping: bool) -> None:
        self._sleeping = sleeping
        self._restart()

    def _restart(self) -> None:
        self._ensure_visible()
        if not self._timer.isActive():
            self._timer.start()

    def _ensure_visible(self) -> None:
        if not self.isVisible():
            self.show()
        self.raise_()

    def _tick(self) -> None:
        dt = self._timer.interval() / 1000.0
        alive: list[Particle] = []
        for particle in self._particles:
            particle.life += dt
            if particle.life >= particle.ttl:
                continue
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.vy += 9 * dt
            alive.append(particle)
        self._particles = alive
        if not self._particles and not self._sleeping:
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for particle in self._particles:
            progress = particle.life / particle.ttl
            alpha = 1.0 - progress
            if particle.kind == "heart":
                painter.save()
                float_y = math.sin(particle.life * 3.2 + particle.phase) * 7
                painter.translate(particle.x + float_y, particle.y)
                scale = particle.size * (0.75 + 0.25 * math.sin(progress * math.pi))
                painter.scale(scale, scale)
                color = QColor(particle.color)
                color.setAlphaF(max(0.0, alpha) * 0.9)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawPath(self._heart)
                painter.restore()
            else:
                color = QColor(particle.color)
                color.setAlphaF(max(0.0, alpha))
                painter.setPen(QPen(color, 1.4))
                s = particle.size * (1.0 + progress * 0.8)
                painter.drawLine(QPointF(particle.x - s, particle.y), QPointF(particle.x + s, particle.y))
                painter.drawLine(QPointF(particle.x, particle.y - s), QPointF(particle.x, particle.y + s))

        if self._sleeping and self._pet_rect.isValid():
            t = self._clock.elapsed() / 1000.0
            base_x = self._pet_rect.center().x() + self._pet_rect.width() * 0.23
            base_y = self._pet_rect.top() + self._pet_rect.height() * 0.24
            font = QFont(FONT_FAMILY, 12, QFont.Weight.Bold)
            painter.setFont(font)
            for index in range(3):
                local_t = (t * 0.55 + index * 0.33) % 1.0
                alpha = int(210 * (1.0 - local_t))
                if alpha <= 5:
                    continue
                color = QColor(245, 231, 235, alpha)
                painter.setPen(color)
                x = base_x + local_t * 30 + index * 7
                y = base_y - local_t * 58 - index * 2
                painter.drawText(QPointF(x, y), "Z")


class ClickSpark(QWidget):
    """保留给未来扩展，不参与桌面显示。"""
