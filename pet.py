from __future__ import annotations

import ctypes
import math
import random
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QCursor,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
    QRegion,
)
from PySide6.QtWidgets import QMenu, QWidget

from app_paths import ASSET_DIR
from bubble import SpeechBubble
from config import Config, IDLE_CHATTER_INTERVALS
from effects import EffectOverlay
from local_replies import (
    ACTION_LINES,
    CHAT_OPEN_LINES,
    DRAG_LINES,
    IDLE_LINES,
    PAT_LINES,
    POKE_LINES,
    SLEEP_LINES,
    WAKE_LINES,
)
from system_utils import is_startup_enabled, set_startup_enabled
from theme import MENU_STYLE


class PetWindow(QWidget):
    chat_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    visibility_changed = Signal(bool)
    always_on_top_changed = Signal(bool)
    scale_changed = Signal(float)
    voice_primed = Signal()
    voice_started = Signal()
    voice_finished = Signal()
    voice_cancelled = Signal()
    observe_screen_requested = Signal()
    plugins_requested = Signal()
    update_requested = Signal()
    screen_watch_toggled = Signal(bool)
    petted = Signal()
    poked = Signal()

    VOICE_HOLD_MS = 650

    ACTION_SPECS = {
        "hop": (0.85, True),
        "spin": (1.05, True),
        "stretch": (1.15, True),
        "shake": (0.65, False),
        "lean": (0.90, True),
        "nod": (0.65, False),
    }

    def __init__(self, config: Config) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.config = config
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle("LFELKEEA · 桌面宠物")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("长按我说话，松开就能对话 · 双击打开聊天 · 右键菜单")

        self.scale_factor = max(0.7, min(1.45, float(config.get("scale", 1.0) or 1.0)))
        self._always_on_top_state = bool(config.get("always_on_top", True))
        self.margin = 48
        self._base_height = 430
        self._assets: dict[str, QPixmap] = {}
        self._pix_normal = QPixmap()
        self._pix_blink = QPixmap()
        self._scaled_normal = QPixmap()
        self._scaled_blink = QPixmap()
        self._draw_rect = QRectF()
        self._mask_cleared = False
        self._hit_region = QRegion()
        self._live2d = None
        self._model_frame: QImage | None = None
        self._model_aspect = 0.0
        self._model_active = False
        self._talk_token = 0
        self._gaze_tick = 0
        self._line_override = None
        self._line_override_owner = ""
        self._action_menu_groups: list[dict] = []
        self._action_menu_owner = ""
        self._action_name = ""
        self._action_t0 = -1.0
        self._action_duration = 0.0
        self._action_clear_mask = False
        self._load_assets()
        self._apply_metrics()

        self.bubble = SpeechBubble()
        self.effects = EffectOverlay()
        self._clock = QElapsedTimer()
        self._clock.start()
        self._reset_state()

        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._start_blink)
        self._schedule_blink()

        self._chatter_timer = QTimer(self)
        self._chatter_timer.setSingleShot(True)
        self._chatter_timer.timeout.connect(self._idle_chatter)
        self._schedule_chatter()

        self._idle_action_timer = QTimer(self)
        self._idle_action_timer.setSingleShot(True)
        self._idle_action_timer.timeout.connect(self._idle_action)
        self._schedule_idle_action()

        self._auto_sleep_timer = QTimer(self)
        self._auto_sleep_timer.setInterval(10000)
        self._auto_sleep_timer.timeout.connect(self._check_auto_sleep)
        self._auto_sleep_timer.start()

        self._voice_hold_timer = QTimer(self)
        self._voice_hold_timer.setSingleShot(True)
        self._voice_hold_timer.setInterval(self.VOICE_HOLD_MS)
        self._voice_hold_timer.timeout.connect(self._begin_voice_hold)

        self._chat_open = False
        self._restore_position()
        self.effects.follow(self._global_rect())

    def _reset_state(self) -> None:
        self._sleeping = False
        self._manual_sleep = False
        self._dragging = False
        self._mouse_down = False
        self._moved = False
        self._press_global = QPoint()
        self._press_window = QPoint()
        self._pending_click_token = 0
        self._click_local = QPoint()
        self._last_interaction = time.monotonic()
        self._blink_until = 0.0
        self._bounce_t0 = -1.0
        self._drag_tilt = 0.0
        self._drag_tilt_target = 0.0
        self._hidden_by_user = False
        self._voice_primed = False
        self._voice_active = False
        self._action_name = ""
        self._action_t0 = -1.0
        self._action_duration = 0.0
        self._action_clear_mask = False

    def _load_assets(self) -> None:
        for name in ("character", "character_blink", "avatar"):
            path = ASSET_DIR / f"{name}.png"
            if path.exists():
                self._assets[name] = QPixmap(str(path))
        if "character" not in self._assets:
            raise FileNotFoundError(f"缺少人物素材：{ASSET_DIR / 'character.png'}")
        self._pix_normal = self._assets["character"]
        self._pix_blink = self._assets.get("character_blink", self._pix_normal)

    def _apply_metrics(self) -> None:
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        height = int(self._base_height * self.scale_factor)
        height = min(height, int(area.height() * 0.62))
        if self._model_active and self._model_aspect > 0.05:
            aspect = self._model_aspect
        else:
            aspect = self._pix_normal.width() / max(1, self._pix_normal.height())
        width = max(120, int(height * aspect))
        window_w = width + self.margin * 2
        window_h = height + self.margin * 2
        self.setFixedSize(window_w, window_h)
        self._draw_rect = QRectF(
            (window_w - width) / 2.0,
            window_h - self.margin - height,
            float(width),
            float(height),
        )
        self._scaled_normal = self._pix_normal.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._scaled_blink = self._pix_blink.scaled(
            width,
            height,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._update_mask()

    def _update_mask(self) -> None:
        if self._mask_cleared:
            return
        if self._model_active:
            # Live2D 的轮廓每帧都在动，命中区按绘制区算，避免每帧重算区域卡顿。
            self._hit_region = QRegion(self._draw_rect.toRect())
            return
        bitmap = self._scaled_normal.mask()
        base = QRegion(bitmap)
        region = QRegion(base)
        offsets = [
            (-20, 0),
            (20, 0),
            (0, -20),
            (0, 20),
            (-14, -14),
            (14, -14),
            (-14, 14),
            (14, 14),
            (-10, 0),
            (10, 0),
            (0, -10),
            (0, 10),
        ]
        for dx, dy in offsets:
            region = region.united(base.translated(dx, dy))
        region = region.translated(int(self._draw_rect.left()), int(self._draw_rect.top()))
        # 只用于鼠标命中测试，绝不作为窗口形状，避免把移动中的发丝裁掉
        self._hit_region = region

    def _clear_mask(self) -> None:
        if not self._mask_cleared:
            self._mask_cleared = True
            self._hit_region = QRegion(self.rect())

    def _restore_mask(self) -> None:
        if not self._mask_cleared:
            return
        if self._dragging or self._action_t0 >= 0:
            return
        self._mask_cleared = False
        self._update_mask()

    def _global_rect(self) -> QRect:
        return QRect(self.mapToGlobal(QPoint(0, 0)), self.size())

    def _visible_rect(self) -> QRect:
        rect = self._draw_rect.toRect()
        return QRect(self.mapToGlobal(rect.topLeft()), rect.size())

    def _head_global(self) -> QPoint:
        return QPoint(
            self.x() + self.width() // 2,
            self.y() + int(self._draw_rect.top() + self._draw_rect.height() * 0.12),
        )

    def set_line_override(self, callback, owner: str = "") -> None:
        """插件可以替换内置台词。callback(kind, choices) 返回字符串或空。"""
        self._line_override = callback
        self._line_override_owner = str(owner or "")

    def clear_line_override(self, owner: str = "") -> None:
        if owner and owner != self._line_override_owner:
            return
        self._line_override = None
        self._line_override_owner = ""

    def set_action_menu(self, groups, owner: str = "") -> None:
        """插件可以用自己的动作和表情替换右键的「动作」菜单。

        groups 形如 ``[{"title": "常用", "items": [{"label": "挥手", "callback": fn}]}]``；
        title 为空时直接挂在「动作」下面。
        """
        normalized: list[dict] = []
        for group in groups or []:
            if not isinstance(group, dict):
                continue
            items: list[dict] = []
            for item in group.get("items") or []:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or "").strip()
                callback = item.get("callback")
                if label and callable(callback):
                    items.append({"label": label, "callback": callback})
            if items:
                normalized.append(
                    {"title": str(group.get("title") or "").strip(), "items": items}
                )
        if not normalized:
            return
        self._action_menu_groups = normalized
        self._action_menu_owner = str(owner or "")

    def clear_action_menu(self, owner: str = "") -> None:
        if owner and owner != self._action_menu_owner:
            return
        self._action_menu_groups = []
        self._action_menu_owner = ""

    def _fill_action_menu(self, menu: QMenu) -> dict:
        """填充「动作」菜单，返回 {QAction: 回调}。"""
        callbacks: dict[object, object] = {}
        if self._model_active and self._action_menu_groups:
            for group in self._action_menu_groups:
                title = str(group.get("title") or "")
                if title:
                    submenu = menu.addMenu(title)
                    submenu.setStyleSheet(MENU_STYLE)
                else:
                    submenu = menu
                for item in group.get("items") or []:
                    action = submenu.addAction(str(item.get("label") or ""))
                    callbacks[action] = item.get("callback")
            return callbacks

        for key, label in (
            ("hop", "跳一下"),
            ("spin", "转个圈"),
            ("stretch", "伸个懒腰"),
            ("shake", "抖抖耳朵"),
            ("lean", "歪歪头"),
            ("nod", "点点头"),
        ):
            action = menu.addAction(label)
            callbacks[action] = lambda name=key: self.start_action(name, announce=True)
        return callbacks

    def _line(self, kind: str, choices) -> str:
        options = tuple(choices)
        if self._line_override is not None:
            try:
                text = self._line_override(kind, options)
            except Exception:
                text = ""
            text = str(text or "").strip()
            if text:
                return text
        return random.choice(options)

    @property
    def current_action(self) -> str:
        return self._action_name

    @property
    def sleeping(self) -> bool:
        return self._sleeping

    def say(self, text: str, duration: int = 5200) -> None:
        if self._hidden_by_user:
            return
        self.bubble.show_text(text, self._visible_rect(), duration)
        if self._live2d is not None and self._live2d.is_active:
            self._live2d.set_talking(True)
            self._talk_token += 1
            token = self._talk_token
            QTimer.singleShot(
                max(600, min(5200, int(duration))),
                lambda: self._stop_talking(token),
            )

    def _stop_talking(self, token: int) -> None:
        if token != self._talk_token:
            return
        if self._live2d is not None:
            self._live2d.set_talking(False)

    def _schedule_blink(self) -> None:
        self._blink_timer.start(random.randint(3200, 7600))

    def _start_blink(self) -> None:
        if not self._sleeping:
            self._blink_until = self._clock.elapsed() / 1000.0 + 0.13
            self.update()
        self._schedule_blink()

    def _chatter_level(self) -> str:
        if not self.config.get("idle_chatter", True):
            return "off"
        level = str(self.config.get("idle_chatter_frequency", "normal"))
        return level if level in IDLE_CHATTER_INTERVALS or level == "off" else "normal"

    def _schedule_chatter(self) -> None:
        level = self._chatter_level()
        if level == "off":
            self._chatter_timer.stop()
            return
        low, high = IDLE_CHATTER_INTERVALS[level]
        self._chatter_timer.start(random.randint(low, high))

    def apply_chatter_settings(self) -> None:
        self._chatter_timer.stop()
        self._schedule_chatter()

    def _schedule_idle_action(self) -> None:
        self._idle_action_timer.start(random.randint(28000, 70000))

    def _idle_action(self) -> None:
        if (
            not self._sleeping
            and not self._chat_open
            and not self._hidden_by_user
            and not self._dragging
            and not self._mouse_down
            and not self._voice_active
            and self._action_t0 < 0
            and time.monotonic() - self._last_interaction > 12
        ):
            self.start_action(random.choice(list(self.ACTION_SPECS)), announce=False)
        self._schedule_idle_action()

    def _idle_chatter(self) -> None:
        if (
            not self._sleeping
            and not self._chat_open
            and not self._hidden_by_user
            and not self._voice_active
            and self._chatter_level() != "off"
            and time.monotonic() - self._last_interaction > 55
        ):
            self.say(self._line("idle", IDLE_LINES), 4800)
            self._last_interaction = time.monotonic()
        self._schedule_chatter()

    def _check_auto_sleep(self) -> None:
        if (
            not self._sleeping
            and not self._chat_open
            and not self._hidden_by_user
            and not self._voice_active
            and time.monotonic() - self._last_interaction > 300
        ):
            self._set_sleeping(True, auto=True)

    def _set_sleeping(self, sleeping: bool, auto: bool = False) -> None:
        if self._sleeping == sleeping:
            return
        self._sleeping = sleeping
        self._manual_sleep = sleeping and not auto
        if self._live2d is not None:
            self._live2d.set_sleeping(sleeping)
        self.effects.set_sleeping(sleeping)
        if sleeping:
            self._cancel_voice_hold(notify=True)
            self._finish_action()
            self._chatter_timer.stop()
            self.say(self._line("sleep", SLEEP_LINES), 4200)
        else:
            self.effects.emit_sparkles(6)
            self._last_interaction = time.monotonic()
            self.say(self._line("wake", WAKE_LINES), 3800)
            self._schedule_chatter()
        self.update()

    def _wake(self) -> None:
        if self._sleeping:
            self._set_sleeping(False)

    def _tick(self) -> None:
        now = self._clock.elapsed() / 1000.0
        if self._action_t0 >= 0 and now - self._action_t0 >= self._action_duration:
            self._finish_action()
        self._drag_tilt += (self._drag_tilt_target - self._drag_tilt) * 0.18
        self._gaze_tick += 1
        if self._gaze_tick % 5 == 0:
            self._update_gaze()
        self.update()

    def _update_gaze(self) -> None:
        controller = self._live2d
        if controller is None or not controller.is_active or not controller.auto_gaze:
            return
        cursor = QCursor.pos()
        center_x = self.x() + self.width() / 2.0
        center_y = self.y() + self._draw_rect.top() + self._draw_rect.height() * 0.16
        span_x = max(120.0, self.width() * 0.9)
        span_y = max(120.0, self.height() * 0.6)
        gx = max(-1.0, min(1.0, (cursor.x() - center_x) / span_x))
        gy = max(-1.0, min(1.0, (center_y - cursor.y()) / span_y))
        controller.set_gaze(gx, gy)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        now = self._clock.elapsed() / 1000.0
        model_frame = self._model_frame if self._model_active else None
        bob_amount = 1.1 if model_frame is not None else 2.2
        bob = math.sin(now * 1.9) * bob_amount
        sway = math.sin(now * 0.72) * 0.85
        breath_y = 1.0 - math.sin(now * 1.9) * 0.006
        breath_x = 1.0 + math.sin(now * 1.9) * 0.004

        bounce = 0.0
        if self._bounce_t0 >= 0:
            progress = (now - self._bounce_t0) / 0.62
            if progress >= 1.0:
                self._bounce_t0 = -1.0
            else:
                bounce = -math.sin(progress * math.pi) * 16.0 * (1.0 - progress * 0.35)
                breath_y += math.sin(progress * math.pi * 2) * 0.015

        if self._drag_tilt_target == 0 and not self._mouse_down:
            sway += self._drag_tilt * 0.5
        else:
            sway += self._drag_tilt

        blinking = self._sleeping or now < self._blink_until
        pixmap = self._scaled_blink if blinking else self._scaled_normal
        if self._sleeping:
            painter.setOpacity(0.94)
        action_dx = 0.0
        action_dy = 0.0
        action_angle = 0.0
        action_sx = 1.0
        action_sy = 1.0
        if self._action_t0 >= 0 and self._action_duration > 0:
            progress = min(1.0, max(0.0, (now - self._action_t0) / self._action_duration))
            wave = math.sin(progress * math.pi)
            if self._action_name == "hop":
                action_dy = -wave * 34.0
                action_sy = 1.0 + wave * 0.015
                action_sx = 1.0 - wave * 0.01
            elif self._action_name == "spin":
                action_sx = math.cos(progress * math.tau)
                action_dy = -wave * 10.0
            elif self._action_name == "stretch":
                action_sy = 1.0 + wave * 0.08
                action_sx = 1.0 - wave * 0.04
                action_dy = -wave * 3.0
            elif self._action_name == "shake":
                action_dx = math.sin(progress * math.pi * 9.0) * 8.0 * (1.0 - progress)
            elif self._action_name == "lean":
                action_angle = wave * 9.0
            elif self._action_name == "nod":
                action_dy = math.sin(progress * math.pi * 4.0) * 5.0
                action_angle = math.sin(progress * math.pi * 2.0) * 1.5

        width = self._draw_rect.width()
        height = self._draw_rect.height()
        cx = self.width() / 2.0
        bottom = self._draw_rect.bottom()

        painter.translate(cx + action_dx, bottom + bob + bounce + action_dy)
        painter.rotate(sway + action_angle)
        painter.scale(breath_x * action_sx, breath_y * action_sy)
        target = QRectF(-width / 2.0, -height, width, height)
        if model_frame is not None and not model_frame.isNull():
            painter.drawImage(target, model_frame)
        else:
            painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))

    def _trigger_bounce(self) -> None:
        self._bounce_t0 = self._clock.elapsed() / 1000.0
        self.update()

    def start_action(self, name: str, announce: bool = False) -> None:
        spec = self.ACTION_SPECS.get(name)
        if spec is None:
            return
        if self._hidden_by_user:
            self.show_pet()
        if self._sleeping:
            self._wake()
        if self._action_t0 >= 0:
            self._finish_action()
        duration, clear_mask = spec
        self._action_name = name
        self._action_t0 = self._clock.elapsed() / 1000.0
        self._action_duration = duration
        self._action_clear_mask = clear_mask
        if clear_mask:
            self._clear_mask()
        if announce:
            self._last_interaction = time.monotonic()
            if name in ACTION_LINES:
                self.say(self._line("action:" + name, ACTION_LINES[name]), 3400)
        self.update()

    def _finish_action(self) -> None:
        had_clear_mask = self._action_clear_mask
        self._action_name = ""
        self._action_t0 = -1.0
        self._action_duration = 0.0
        self._action_clear_mask = False
        if had_clear_mask:
            self._restore_mask()

    def _pat(self) -> None:
        self._wake()
        self._last_interaction = time.monotonic()
        self._trigger_bounce()
        self._blink_until = self._clock.elapsed() / 1000.0 + 1.1
        self.effects.follow(self._global_rect())
        head = self._head_global()
        local = QPointF(head.x() - self.effects.x(), head.y() - self.effects.y())
        self.effects.emit_hearts(local, 7)
        self.say(self._line("pat", PAT_LINES), 4800)
        self.petted.emit()

    def _poke(self) -> None:
        self._wake()
        self._last_interaction = time.monotonic()
        self._trigger_bounce()
        self.effects.follow(self._global_rect())
        local = QPointF(self.width() * 0.5, self.height() * 0.55)
        self.effects.emit_sparkles(6)
        self.say(self._line("poke", POKE_LINES), 4200)
        self.poked.emit()

    def _single_click(self, click_local: QPoint, token: int) -> None:
        if token != self._pending_click_token or self._moved or self._sleeping:
            return
        head_limit = self._draw_rect.top() + self._draw_rect.height() * 0.44
        if click_local.y() <= head_limit:
            self._pat()
        else:
            self._poke()

    def _begin_voice_hold(self) -> None:
        if (
            not self._mouse_down
            or self._moved
            or self._dragging
            or self._voice_active
            or self._hidden_by_user
            or not self.isVisible()
        ):
            return
        self._wake()
        self._voice_primed = False
        self._voice_active = True
        self._pending_click_token += 1
        self._last_interaction = time.monotonic()
        self.voice_started.emit()

    def _cancel_voice_hold(self, notify: bool = False) -> None:
        self._voice_hold_timer.stop()
        was_primed = self._voice_primed
        self._voice_primed = False
        if not self._voice_active:
            if notify and was_primed:
                self.voice_cancelled.emit()
            return
        self._voice_active = False
        if notify:
            self.voice_cancelled.emit()

    def _hit_test_global(self, pos: QPoint) -> bool:
        if self._hit_region.isEmpty():
            return True
        return self._hit_region.contains(self.mapFromGlobal(pos))

    def nativeEvent(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG" and not (
            self._mouse_down or self._dragging or self._action_t0 >= 0
        ):
            try:
                msg = wintypes.MSG.from_address(int(message))
            except Exception:
                msg = None
            if msg is not None and msg.message == 0x0084:  # WM_NCHITTEST
                if not self._hit_test_global(QCursor.pos()):
                    return True, -1  # HTTRANSPARENT
        return super().nativeEvent(event_type, message)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._wake()
            self._last_interaction = time.monotonic()
            self._mouse_down = True
            self._moved = False
            self._press_global = event.globalPosition().toPoint()
            self._press_window = self.pos()
            self._click_local = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if not self._voice_active:
                self._voice_primed = True
                self.voice_primed.emit()
            self._voice_hold_timer.start()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._mouse_down:
            delta = event.globalPosition().toPoint() - self._press_global
            if not self._moved and delta.manhattanLength() > 7:
                self._moved = True
                self._dragging = True
                self._pending_click_token += 1
                self._cancel_voice_hold(notify=True)
                self.bubble.hide_now()
                self._clear_mask()
            if self._moved:
                new_pos = self._press_window + delta
                self.move(new_pos)
                self._drag_tilt_target = max(-5.0, min(5.0, delta.x() * 0.045))
                self.effects.follow(self._global_rect())
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._voice_hold_timer.stop()
            was_voice = self._voice_active
            was_primed = self._voice_primed
            self._voice_active = False
            self._voice_primed = False
            self._mouse_down = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            moved = self._moved
            self._moved = False
            self._dragging = False
            self._drag_tilt = 0.0
            self._drag_tilt_target = 0.0
            self._restore_mask()
            self._last_interaction = time.monotonic()
            if was_voice:
                self.voice_finished.emit()
                event.accept()
                return
            if was_primed:
                self.voice_cancelled.emit()
            if moved:
                self._clamp_to_screen()
                self.config.set("position", [self.x(), self.y()], save=True)
                self.effects.follow(self._global_rect())
                if random.random() < 0.45:
                    self.say(self._line("drag", DRAG_LINES), 3800)
                event.accept()
                return
            token = self._pending_click_token + 1
            self._pending_click_token = token
            QTimer.singleShot(240, lambda: self._single_click(self._click_local, token))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pending_click_token += 1
            self._cancel_voice_hold(notify=True)
            self._wake()
            self._last_interaction = time.monotonic()
            self._trigger_bounce()
            self.say(self._line("chat_open", CHAT_OPEN_LINES), 3200)
            self.chat_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            step = 0.06 if event.angleDelta().y() > 0 else -0.06
            self.set_scale(self.scale_factor + step)
            event.accept()
            return
        super().wheelEvent(event)

    def set_scale(self, new_scale: float) -> None:
        new_scale = max(0.7, min(1.45, float(new_scale)))
        if abs(new_scale - self.scale_factor) < 0.001:
            return
        bottom_center = QPoint(self.x() + self.width() // 2, self.y() + self.height())
        self.scale_factor = new_scale
        self.config.set("scale", round(new_scale, 3), save=True)
        self._apply_metrics()
        self.move(bottom_center.x() - self.width() // 2, bottom_center.y() - self.height())
        self._clamp_to_screen()
        self.effects.follow(self._global_rect())
        self.scale_changed.emit(new_scale)
        self.say(f"大小：{int(new_scale * 100)}%", 2400)

    # ---------- Live2D 模型 ----------

    def _ensure_live2d(self):
        if self._live2d is not None:
            return self._live2d
        from live2d_model import Live2DController

        controller = Live2DController(self)
        controller.frame_ready.connect(self._on_model_frame)
        controller.loaded.connect(self._on_model_loaded)
        controller.failed.connect(self._on_model_failed)
        self._live2d = controller
        return controller

    def load_live2d(
        self,
        path: str,
        *,
        owner: str = "",
        height: int = 720,
        fps: int = 30,
        scale: float = 1.0,
        offset: tuple[float, float] = (0.0, 0.0),
        follow_cursor: bool = True,
    ) -> bool:
        controller = self._ensure_live2d()
        controller.set_follow_cursor(bool(follow_cursor))
        controller.set_sleeping(self._sleeping)
        controller.load(
            path,
            owner=owner,
            height=height,
            fps=fps,
            scale=scale,
            offset=offset,
        )
        return True

    def unload_live2d(self, owner: str = "") -> bool:
        controller = self._live2d
        if controller is None:
            return True
        if owner and controller.owner and controller.owner != owner:
            return False
        controller.unload()
        self._model_active = False
        self._model_frame = None
        self._model_aspect = 0.0
        self.clear_action_menu()
        self._apply_metrics()
        self.update()
        return True

    def model_info(self) -> dict:
        controller = self._live2d
        if controller is None:
            return {"active": False, "owner": "", "width": 0, "height": 0}
        info = controller.info
        return {
            "active": controller.is_active,
            "owner": controller.owner,
            "width": int(info.get("width") or 0),
            "height": int(info.get("height") or 0),
            "error": controller.last_error,
        }

    def _on_model_frame(self, image: QImage) -> None:
        if image.isNull():
            return
        self._model_frame = image
        self.update()

    def _on_model_loaded(self, info: dict) -> None:
        width = float(info.get("width") or 0)
        height = float(info.get("height") or 0)
        if height > 1 and width > 1:
            self._model_aspect = width / height
        self._model_active = True
        self._mask_cleared = False
        self._apply_metrics()
        self.update()

    def _on_model_failed(self, message: str) -> None:
        if not message:
            return
        self._model_active = False
        self._model_frame = None
        self._model_aspect = 0.0
        self._apply_metrics()
        self.update()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self._pending_click_token += 1
        self._cancel_voice_hold(notify=True)
        self.bubble.hide_now()
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        menu.setStyleSheet(MENU_STYLE)

        pat_action = menu.addAction("摸摸头")
        chat_action = menu.addAction("和 LFELKEEA 聊天")
        observe_action = menu.addAction("看看屏幕，说句话")
        voice_help_action = menu.addAction("长按我：语音对话")
        menu.addSeparator()

        action_menu = menu.addMenu("动作")
        action_menu.setStyleSheet(MENU_STYLE)
        action_callbacks = self._fill_action_menu(action_menu)

        size_menu = menu.addMenu("显示大小")
        size_menu.setStyleSheet(MENU_STYLE)
        size_items: dict[object, int] = {}
        for pct in (70, 85, 100, 115, 130, 145):
            act = size_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(abs(self.scale_factor * 100 - pct) < 1.0)
            size_items[act] = pct

        menu.addSeparator()
        screen_state = self.config.get("screen_watch", {})
        screen_enabled = bool(screen_state.get("enabled", False)) if isinstance(screen_state, dict) else False
        screen_action = menu.addAction("定时看屏幕")
        screen_action.setCheckable(True)
        screen_action.setChecked(screen_enabled)
        plugins_action = menu.addAction("插件管理")
        update_action = menu.addAction("检查更新…")
        sleep_action = menu.addAction("叫醒 LFELKEEA" if self._sleeping else "让 LFELKEEA 休息")
        settings_action = menu.addAction("AI 与桌宠设置")
        menu.addSeparator()
        top_action = menu.addAction("始终置顶")
        top_action.setCheckable(True)
        top_action.setChecked(bool(self.config.get("always_on_top", True)))
        startup_action = menu.addAction("开机自动启动")
        startup_action.setCheckable(True)
        startup_action.setChecked(is_startup_enabled())
        menu.addSeparator()
        hide_action = menu.addAction("隐藏 LFELKEEA")
        quit_action = menu.addAction("退出")

        chosen = menu.exec_(event.globalPos())
        if chosen is pat_action:
            self._pat()
        elif chosen is chat_action:
            self.chat_requested.emit()
        elif chosen is observe_action:
            self.observe_screen_requested.emit()
        elif chosen is plugins_action:
            self.plugins_requested.emit()
        elif chosen is update_action:
            self.update_requested.emit()
        elif chosen is screen_action:
            self.screen_watch_toggled.emit(screen_action.isChecked())
        elif chosen is voice_help_action:
            self.say("按住我别松手，说完再松开，就能直接和我说话。", 5600)
        elif chosen in action_callbacks:
            try:
                action_callbacks[chosen]()
            except Exception:
                pass
        elif chosen in size_items:
            self.set_scale(size_items[chosen] / 100.0)
        elif chosen is sleep_action:
            self._set_sleeping(not self._sleeping)
        elif chosen is settings_action:
            self.settings_requested.emit()
        elif chosen is top_action:
            self.set_always_on_top(top_action.isChecked())
        elif chosen is startup_action:
            ok = set_startup_enabled(startup_action.isChecked())
            if ok:
                self.say("好，开机时我会自己出来。", 3000)
            else:
                self.say("设置开机启动失败了。", 3000)
        elif chosen is hide_action:
            self.hide_pet()
        elif chosen is quit_action:
            self.quit_requested.emit()

    def set_always_on_top(self, enabled: bool) -> None:
        changed = self._always_on_top_state != enabled
        self.config.set("always_on_top", enabled, save=True)
        self.always_on_top_changed.emit(enabled)
        if not changed:
            return
        self._always_on_top_state = enabled
        geometry = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()
        self.setGeometry(geometry)
        self.effects.raise_()

    def hide_pet(self) -> None:
        self._hidden_by_user = True
        self._cancel_voice_hold(notify=True)
        self._finish_action()
        self.bubble.hide_now()
        self.effects.hide()
        self.hide()
        self.visibility_changed.emit(False)

    def show_pet(self) -> None:
        self._hidden_by_user = False
        self.show()
        self.raise_()
        self.effects.follow(self._global_rect())
        self.visibility_changed.emit(True)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide_pet()
        else:
            self.show_pet()

    def set_chat_open(self, opened: bool) -> None:
        self._chat_open = opened
        self._last_interaction = time.monotonic()
        self._schedule_chatter()

    def _restore_position(self) -> None:
        screen = QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        saved = self.config.get("position")
        x = area.right() - self.width() - 70
        y = area.bottom() - self.height() - 8
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            try:
                x, y = int(saved[0]), int(saved[1])
            except (TypeError, ValueError):
                pass
        self.move(x, y)
        self._clamp_to_screen()

    def _clamp_to_screen(self) -> None:
        screen = QGuiApplication.screenAt(self._global_rect().center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        edge_pad = 8
        min_x = area.left() + edge_pad - self.margin
        max_x = area.right() + 1 - edge_pad - self.width() + self.margin
        min_y = area.top() - self.margin
        max_y = area.bottom() + 1 - edge_pad - self.height() + self.margin
        x = min(max(self.x(), min_x), max_x)
        y = min(max(self.y(), min_y), max_y)
        if x != self.x() or y != self.y():
            self.move(x, y)

    def cleanup(self) -> None:
        self._timer.stop()
        self._blink_timer.stop()
        self._chatter_timer.stop()
        self._idle_action_timer.stop()
        self._auto_sleep_timer.stop()
        self._voice_hold_timer.stop()
        self._pending_click_token += 1
        self.bubble.hide_now()
        self.bubble.close()
        self.effects.close()
        if self._live2d is not None:
            self._live2d.unload()
            self._live2d = None
        self._model_frame = None
        self._model_active = False
        self._action_menu_groups = []
        self._action_menu_owner = ""

    def icon(self) -> QIcon:
        avatar = self._assets.get("avatar")
        if avatar and not avatar.isNull():
            return QIcon(avatar)
        return QIcon(self._scaled_normal)


def get_chat_anchor(pet: PetWindow) -> QPoint:
    rect = pet._visible_rect()
    return QPoint(rect.center().x(), rect.top())
