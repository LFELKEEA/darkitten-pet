from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app_paths import resource_dir
from plugin_manager import PluginInfo, PluginManager
from theme import ACCENT, BORDER, DIALOG_STYLE, MUTED, TEXT
from ui_widgets import DraggableHeader


class PluginDialog(QDialog):
    def __init__(self, manager: PluginManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("LFELKEEA · 插件")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.resize(580, 640)
        self.setMinimumSize(520, 520)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self.manager.changed.connect(self._queue_refresh)
        self.manager.updates_checked.connect(self._on_updates_checked)
        self.manager.plugin_updated.connect(self._on_plugin_updated)
        self.refresh()

    def _queue_refresh(self) -> None:
        QTimer.singleShot(0, self.refresh)

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
        title = QLabel("插件")
        title.setObjectName("title")
        self.summary = QLabel("")
        self.summary.setObjectName("muted")
        title_box.addWidget(title)
        title_box.addWidget(self.summary)
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

        warning = QLabel(
            "插件是本机 Python 代码，能读取你允许它读取的内容。只启用你信任的插件；"
            "新插件默认不会自动运行。"
        )
        warning.setObjectName("muted")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.scroll.viewport().setStyleSheet("background: transparent;")
        self.host = QWidget()
        self.host.setStyleSheet("background: transparent;")
        self.list_layout = QVBoxLayout(self.host)
        self.list_layout.setContentsMargins(0, 2, 0, 2)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.host)
        layout.addWidget(self.scroll, 1)

        self.update_status = QLabel("")
        self.update_status.setObjectName("muted")
        self.update_status.setWordWrap(True)
        layout.addWidget(self.update_status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        folder_btn = QPushButton("打开插件目录")
        folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        folder_btn.clicked.connect(self._open_folder)
        reload_btn = QPushButton("重新扫描")
        reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        reload_btn.clicked.connect(self._reload_all)
        docs_btn = QPushButton("开发文档")
        docs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        docs_btn.clicked.connect(self._open_docs)
        self.check_update_btn = QPushButton("检查更新")
        self.check_update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_update_btn.clicked.connect(self._check_updates)
        self.update_all_btn = QPushButton("全部更新")
        self.update_all_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_all_btn.clicked.connect(self._update_all)
        self.update_all_btn.setVisible(False)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primary")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(folder_btn)
        bottom.addWidget(reload_btn)
        bottom.addWidget(docs_btn)
        bottom.addWidget(self.check_update_btn)
        bottom.addWidget(self.update_all_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _center_on_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(
            area.center().x() - self.width() // 2,
            area.center().y() - self.height() // 2,
        )

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_positioned", False):
            self._positioned = True
            self._center_on_screen()

    def _clear_list(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def refresh(self) -> None:
        self.manager.discover()
        plugins = self.manager.plugins()
        running = sum(1 for item in plugins if item.status == "运行中")
        updates = self.manager.available_update_ids()
        summary = f"{len(plugins)} 个插件 · {running} 个运行中"
        if updates:
            summary += f" · {len(updates)} 个可更新"
        self.summary.setText(summary)
        self.update_all_btn.setVisible(len(updates) > 1)
        self._clear_list()
        if not plugins:
            empty = QLabel("还没有插件。把插件文件夹放到插件目录，然后点“重新扫描”。")
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.list_layout.insertWidget(0, empty)
            return
        for info in plugins:
            self.list_layout.insertWidget(self.list_layout.count() - 1, self._build_row(info))

    def _build_row(self, info: PluginInfo) -> QWidget:
        card = QFrame()
        card.setObjectName("pluginCard")
        card.setStyleSheet(
            f"""
            QFrame#pluginCard {{
                background-color: #1a1316;
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
            """
        )
        row = QVBoxLayout(card)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(f"{info.name}  <span style='color:{MUTED};font-size:10px;'>v{info.version}</span>")
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 600; background: transparent;")
        status = QLabel(self._status_text(info))
        status.setStyleSheet(
            f"color: {self._status_color(info)}; font-size: 10px; background: transparent;"
        )
        enable = QCheckBox("启用")
        enable.setChecked(bool(info.enabled))
        enable.setEnabled(info.status != "清单无效")
        enable.toggled.connect(
            lambda checked, pid=info.id: self._toggle(pid, checked)
        )
        top.addWidget(name, 1)
        top.addWidget(status)
        top.addWidget(enable)
        row.addLayout(top)

        meta = QLabel(f"{info.id} · 作者：{info.author or '未填写'}")
        meta.setObjectName("muted")
        meta.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        row.addWidget(meta)

        if info.description:
            desc = QLabel(info.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {TEXT}; font-size: 11px; background: transparent;")
            row.addWidget(desc)

        perms = QLabel(f"权限：{info.permissions_text}")
        perms.setWordWrap(True)
        perms.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        row.addWidget(perms)

        if info.error or info.status == "清单无效":
            error = QLabel(info.error or "清单无效")
            error.setWordWrap(True)
            error.setStyleSheet("color: #e28189; font-size: 10px; background: transparent;")
            row.addWidget(error)

        release = self.manager.available_release(info.id)
        if release is not None:
            update_row = QHBoxLayout()
            update_row.setSpacing(8)
            note = QLabel(f"有新版本 v{release.version}")
            note.setStyleSheet("color: #e0b45b; font-size: 10px; background: transparent;")
            update_btn = QPushButton(f"更新到 v{release.version}")
            update_btn.setObjectName("primary")
            update_btn.setFixedHeight(24)
            update_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            update_btn.clicked.connect(
                lambda checked=False, pid=info.id, btn=update_btn: self._update_one(pid, btn)
            )
            update_row.addWidget(note)
            update_row.addStretch(1)
            update_row.addWidget(update_btn)
            row.addLayout(update_row)

        if info.enabled and info.status in ("运行中", "运行出错（已隔离）"):
            actions = QHBoxLayout()
            actions.addStretch(1)
            if info.status == "运行中" and self.manager.has_ui(info.id):
                settings_btn = QPushButton("设置")
                settings_btn.setObjectName("primary")
                settings_btn.setFixedHeight(24)
                settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                settings_btn.clicked.connect(
                    lambda checked=False, pid=info.id: self.manager.open_ui(pid, self)
                )
                actions.addWidget(settings_btn)
            reload_btn = QPushButton("重载")
            reload_btn.setObjectName("ghost")
            reload_btn.setFixedHeight(24)
            reload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            reload_btn.clicked.connect(lambda checked=False, pid=info.id: self.manager.reload_plugin(pid))
            actions.addWidget(reload_btn)
            row.addLayout(actions)

        return card

    def _status_text(self, info: PluginInfo) -> str:
        if info.status == "清单无效":
            return "清单无效"
        if info.error and info.status == "运行中":
            return "运行中（有错误）"
        return info.status

    def _status_color(self, info: PluginInfo) -> str:
        if info.status == "运行中" and not info.error:
            return "#8ed6a2"
        if info.status in ("清单无效", "加载失败") or info.error:
            return "#e28189"
        return MUTED

    def _toggle(self, plugin_id: str, enabled: bool) -> None:
        self.manager.set_enabled(plugin_id, enabled)

    def _reload_all(self) -> None:
        self.manager.reload_all()
        self.refresh()

    def _check_updates(self) -> None:
        if self.manager.checking_updates:
            return
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("检查中…")
        self.update_status.setText("正在从插件库检查更新…")
        if not self.manager.check_plugin_updates():
            self.check_update_btn.setEnabled(True)
            self.check_update_btn.setText("检查更新")

    def _on_updates_checked(self, releases: object, error: str) -> None:
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("检查更新")
        if error:
            self.update_status.setText(f"检查更新失败：{error}")
        else:
            count = len(self.manager.available_update_ids())
            self.update_status.setText(
                f"发现 {count} 个可更新的插件。" if count else "所有插件都是最新版本。"
            )
        QTimer.singleShot(0, self.refresh)

    def _on_plugin_updated(self, plugin_id: str, error: str) -> None:
        if error:
            self.update_status.setText(f"{plugin_id} 更新失败：{error}")
        else:
            self.update_status.setText(f"{plugin_id} 已更新完成。")
        self.update_all_btn.setEnabled(True)
        QTimer.singleShot(0, self.refresh)

    def _update_one(self, plugin_id: str, button: QPushButton) -> None:
        button.setEnabled(False)
        button.setText("更新中…")
        self.update_status.setText(f"正在更新 {plugin_id}…")
        self.manager.update_plugins([plugin_id])

    def _update_all(self) -> None:
        plugin_ids = self.manager.available_update_ids()
        if not plugin_ids:
            self.update_status.setText("没有可更新的插件。")
            return
        self.update_all_btn.setEnabled(False)
        self.update_status.setText(f"正在更新 {len(plugin_ids)} 个插件…")
        self.manager.update_plugins(plugin_ids)

    def _open_folder(self) -> None:
        self.manager.plugins_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.manager.plugins_dir)))

    def _open_docs(self) -> None:
        path = resource_dir() / "docs" / "PLUGIN_DEV.md"
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self.summary.setText("没有找到插件开发文档，请查看插件目录里的 README。")
