from __future__ import annotations

"""桌宠更新界面与后台任务。"""

import sys
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from theme import ACCENT, BORDER, DIALOG_STYLE, MUTED, TEXT
from ui_widgets import DraggableHeader
from updater import (
    PetRelease,
    UpdateError,
    check_for_update,
    download_update,
    format_size,
    prepare_update,
    schedule_apply,
    updates_dir,
)
from version import APP_VERSION


class UpdateManager(QObject):
    """在后台线程里检查、下载并准备更新，结果通过信号回到主线程。"""

    checked = Signal(object, str, bool)  # release | None, error, manual
    progress = Signal(int, str)
    ready = Signal(str)  # payload root
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.release: PetRelease | None = None
        self.payload_root = ""
        self._busy = False
        self._cancel = threading.Event()
        self.checked.connect(self._finish_check)
        self.ready.connect(self._reset)
        self.failed.connect(self._reset)

    @property
    def busy(self) -> bool:
        return self._busy

    def _set_busy(self, value: bool) -> None:
        if self._busy == value:
            return
        self._busy = value
        self.busy_changed.emit(value)

    def _finish_check(self, release: object, error: str, manual: bool) -> None:
        self._set_busy(False)

    def check(self, manual: bool = False, timeout: int = 20) -> bool:
        if self._busy:
            return False
        self._set_busy(True)
        self._cancel.clear()

        def worker() -> None:
            try:
                release = check_for_update(APP_VERSION, timeout=timeout)
            except UpdateError as exc:
                self.checked.emit(None, str(exc), manual)
                return
            except Exception as exc:
                self.checked.emit(None, f"检查更新失败：{exc}", manual)
                return
            self.checked.emit(release, "", manual)

        threading.Thread(target=worker, name="pet-update-check", daemon=True).start()
        return True

    def download(self, release: PetRelease) -> bool:
        if self._busy:
            return False
        self.release = release
        self._set_busy(True)
        self._cancel.clear()

        def on_progress(percent: int, text: str) -> None:
            self.progress.emit(int(percent), str(text))

        def worker() -> None:
            try:
                archive = download_update(
                    release,
                    updates_dir(),
                    progress_cb=on_progress,
                    cancel_event=self._cancel,
                )
                payload = prepare_update(archive, updates_dir(), release.version)
            except UpdateError as exc:
                self.failed.emit(str(exc))
                return
            except Exception as exc:
                self.failed.emit(f"更新失败：{exc}")
                return
            self.payload_root = str(payload)
            self.ready.emit(self.payload_root)

        threading.Thread(target=worker, name="pet-update-download", daemon=True).start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def _reset(self) -> None:
        self._set_busy(False)

    def schedule_apply(self, restart: bool = True) -> None:
        if not self.payload_root:
            raise UpdateError("还没有准备好更新包")
        if not getattr(sys, "frozen", False):
            raise UpdateError("源码运行模式不支持自动替换，请手动下载安装包更新。")
        schedule_apply(self.payload_root, restart=restart)
        self._reset()


class UpdateDialog(QDialog):
    ignore_requested = Signal(str)

    def __init__(
        self,
        manager: UpdateManager,
        release: PetRelease | None,
        *,
        current_version: str = APP_VERSION,
        error: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.release = release
        self.current_version = current_version
        self.error = error
        self._applied = False
        self.setWindowTitle("LFELKEEA · 更新")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.resize(500, 440)
        self.setMinimumSize(460, 380)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self._sync_state()
        manager.progress.connect(self._on_progress)
        manager.ready.connect(self._on_ready)
        manager.failed.connect(self._on_failed)
        manager.busy_changed.connect(self._on_busy)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(0)
        card = QFrame()
        card.setObjectName("card")
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(10)

        header = DraggableHeader(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("软件更新")
        title.setObjectName("title")
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(self.subtitle)
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

        self.version_label = QLabel("")
        self.version_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.version_label)

        self.notes_scroll = QScrollArea()
        self.notes_scroll.setWidgetResizable(True)
        self.notes_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.notes_scroll.setMaximumHeight(210)
        self.notes_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.notes_scroll.viewport().setStyleSheet("background: transparent;")
        self.notes_host = QWidget()
        self.notes_host.setStyleSheet("background: transparent;")
        notes_layout = QVBoxLayout(self.notes_host)
        notes_layout.setContentsMargins(0, 0, 2, 0)
        notes_layout.setSpacing(6)
        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        self.notes_label.setTextFormat(Qt.TextFormat.RichText)
        notes_layout.addWidget(self.notes_label)
        notes_layout.addStretch(1)
        self.notes_scroll.setWidget(self.notes_host)
        layout.addWidget(self.notes_scroll, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(16)
        self.progress.setStyleSheet(
            f"""
            QProgressBar {{
                background: #1a1316;
                border: 1px solid {BORDER};
                border-radius: 8px;
                color: {MUTED};
                font-size: 10px;
                text-align: center;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: 7px;
            }}
            """
        )
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.ignore_btn = QPushButton("忽略此版本")
        self.ignore_btn.setObjectName("ghost")
        self.ignore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ignore_btn.clicked.connect(self._ignore)
        self.later_btn = QPushButton("稍后")
        self.later_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.later_btn.clicked.connect(self.reject)
        self.action_btn = QPushButton("立即更新")
        self.action_btn.setObjectName("primary")
        self.action_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.action_btn.clicked.connect(self._primary)
        bottom.addWidget(self.ignore_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.later_btn)
        bottom.addWidget(self.action_btn)
        layout.addLayout(bottom)

        self._center_on_screen()

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.center().x() - self.width() // 2,
            area.center().y() - self.height() // 2,
        )

    # ---------- 状态 ----------

    def _sync_state(self) -> None:
        release = self.release
        if release is None:
            self.subtitle.setText("检查更新")
            self.version_label.setText(
                f"<span style='color:{MUTED};font-size:11px;'>当前版本"
                f"</span> <b>v{self.current_version}</b>"
            )
            self.notes_label.setText(
                f"<span style='color:#e28189;'>{self.error}</span>"
                if self.error
                else f"<span style='color:{MUTED};'>已经是最新版本。</span>"
            )
            self.ignore_btn.setVisible(False)
            self.action_btn.setText("知道了")
            self.action_btn.clicked.disconnect()
            self.action_btn.clicked.connect(self.accept)
            self.status.setText("")
            return

        notes = "".join(
            f"<div style='margin-bottom:4px;'>• {self._escape(item)}</div>"
            for item in release.notes
        ) or f"<span style='color:{MUTED};'>这次没有填写更新说明。</span>"
        self.subtitle.setText(f"发现新版本 v{release.version}")
        self.version_label.setText(
            f"<span style='color:{MUTED};font-size:11px;'>当前版本</span>"
            f" <b>v{self.current_version}</b>"
            f" <span style='color:{MUTED};'>→</span>"
            f" <b style='color:{ACCENT};'>v{release.version}</b>"
            f"<span style='color:{MUTED};font-size:10px;'>"
            f" &nbsp;{format_size(release.size)}"
            f"{(' · ' + release.released) if release.released else ''}</span>"
        )
        self.notes_label.setText(notes)
        self.status.setText("下载后只替换程序文件，data/config.json 和插件数据会保留。")

    @staticmethod
    def _escape(value: str) -> str:
        text = str(value or "")
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _primary(self) -> None:
        if self.release is None:
            return
        if self._applied:
            self._apply_and_restart()
            return
        if self.manager.busy:
            return
        self.action_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.ignore_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.status.setText("准备下载…")
        if not self.manager.download(self.release):
            self._on_failed("上一次更新还没结束，请稍后再试。")

    def _apply_and_restart(self) -> None:
        try:
            self.manager.schedule_apply(restart=True)
        except UpdateError as exc:
            self.status.setText(f"无法启动更新：{exc}")
            return
        self.status.setText("桌宠即将退出，替换文件后会自动重新启动。")
        self.action_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self._quit_application()

    def _quit_application(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return
        controller = getattr(app, "_rin_controller", None)
        if controller is not None and hasattr(controller, "quit"):
            controller.quit()
        else:
            app.quit()

    def _on_busy(self, busy: bool) -> None:
        if busy:
            return
        if not self.action_btn.isEnabled() and not self._applied:
            self.action_btn.setEnabled(True)

    def _on_progress(self, percent: int, text: str) -> None:
        self.progress.setVisible(True)
        self.progress.setValue(max(0, min(100, int(percent))))
        self.progress.setFormat(f"{percent}%")
        if text:
            self.status.setText(text)

    def _on_ready(self, _payload: str) -> None:
        self._applied = True
        self.progress.setValue(100)
        self.progress.setFormat("100%")
        self.status.setText("更新包已下载并校验完成。点「重启并更新」继续。")
        self.action_btn.setText("重启并更新")
        self.action_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.ignore_btn.setEnabled(True)

    def _on_failed(self, error: str) -> None:
        self.progress.setVisible(False)
        self.status.setText(f"更新失败：{error}")
        self.action_btn.setText("重试")
        self.action_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.ignore_btn.setEnabled(True)

    def _ignore(self) -> None:
        if self.release is not None:
            self.ignored_version = self.release.version
            self.ignore_requested.emit(self.release.version)
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._disconnect()
        super().closeEvent(event)

    def _disconnect(self) -> None:
        for signal, slot in (
            (self.manager.progress, self._on_progress),
            (self.manager.ready, self._on_ready),
            (self.manager.failed, self._on_failed),
            (self.manager.busy_changed, self._on_busy),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def reject(self) -> None:
        if self.manager.busy:
            self.manager.cancel()
        super().reject()
