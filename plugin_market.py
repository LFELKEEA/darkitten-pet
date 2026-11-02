from __future__ import annotations

import html
import urllib.parse

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from plugin_manager import PERMISSION_LABELS, PluginManager
from plugin_updater import PluginRelease
from theme import ACCENT, BORDER, DIALOG_STYLE, MUTED, TEXT
from ui_widgets import DraggableHeader
from updater import format_size, is_newer


class MarketplaceDialog(QDialog):
    """在线插件市场：读取插件库清单，下载并安装到 data/plugins。"""

    def __init__(self, manager: PluginManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._query = ""
        self._error = ""
        self._buttons: dict[str, QPushButton] = {}
        self._positioned = False
        self.setWindowTitle("LFELKEEA · 插件市场")
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.resize(620, 700)
        self.setMinimumSize(540, 540)
        self.setStyleSheet(DIALOG_STYLE)
        self._build_ui()
        self.manager.marketplace_loaded.connect(self._on_loaded)
        self.manager.plugin_installed.connect(self._on_installed)
        self.manager.plugin_install_progress.connect(self._on_progress)
        self.refresh()
        QTimer.singleShot(0, self._ensure_loaded)

    # ---------- 构建界面 ----------

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
        title = QLabel("插件市场")
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

        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索名称、id 或描述")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_query)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.clicked.connect(self._reload)
        tools.addWidget(self.search, 1)
        tools.addWidget(self.refresh_btn)
        layout.addLayout(tools)

        warning = QLabel(
            "市场里的插件来自官方插件库。下载只负责安装，不会自动运行；"
            "启用前请先看清权限和外部依赖。"
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

        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        site_btn = QPushButton("打开网页版")
        site_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        site_btn.clicked.connect(self._open_site)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primary")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        bottom.addWidget(site_btn)
        bottom.addStretch(1)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    # ---------- 数据 ----------

    def _ensure_loaded(self) -> None:
        # 每次打开市场都重新拉一次清单。只依赖内存缓存会让窗口一直显示
        # 程序启动时那一版，安装时也会下到旧版本；更新检查是实时请求，
        # 所以会出现「市场 1.4.0、更新检查 1.6.1」的不一致。
        if self.manager.load_marketplace():
            self.refresh_btn.setEnabled(False)
            self.status.setText(
                "正在刷新插件库…"
                if self.manager.marketplace_releases()
                else "正在读取插件库…"
            )

    def _reload(self) -> None:
        self._error = ""
        if self.manager.load_marketplace():
            self.status.setText("正在刷新插件库…")
            self.refresh_btn.setEnabled(False)
        else:
            self.status.setText("正在读取插件库，请稍候。")

    def _on_loaded(self, releases: object, error: str) -> None:
        self.refresh_btn.setEnabled(True)
        self._error = error
        self.refresh()
        if error:
            if self.manager.marketplace_releases():
                self.summary.setText("刷新失败，显示上次同步的清单")
                self.status.setText(f"刷新失败：{error}")
            else:
                self.summary.setText("插件库读取失败")
                self.status.setText(f"读取失败：{error}")
        else:
            self.status.setText("插件库已同步。安装后需要手动启用，插件才会运行。")

    def refresh(self) -> None:
        self.manager.discover()
        releases = self.manager.marketplace_releases()
        installed = sum(
            1
            for release in releases
            if (info := self.manager.get(release.id)) is not None
            and info.status != "清单无效"
        )
        if releases:
            self.summary.setText(f"{len(releases)} 个插件 · {installed} 个已安装")
        elif self._error:
            self.summary.setText("插件库读取失败")
        elif not self.status.text():
            self.summary.setText("正在读取插件库…")
        self._render(releases)

    def _on_query(self, text: str) -> None:
        self._query = str(text or "").strip().lower()
        self._render(self.manager.marketplace_releases())

    def _render(self, releases: list[PluginRelease] | None = None) -> None:
        if releases is None:
            releases = self.manager.marketplace_releases()
        self._clear_list()
        self._buttons.clear()
        query = self._query
        shown = []
        for release in releases:
            haystack = " ".join(
                (release.name, release.id, release.description, release.author)
            ).lower()
            if query and query not in haystack:
                continue
            shown.append(release)
        if not shown:
            if releases:
                text = "没有匹配的插件，换个关键词试试。"
            elif self._error:
                text = f"插件库读取失败：{self._error}"
            else:
                text = "正在读取插件库…"
            empty = QLabel(text)
            empty.setObjectName("muted")
            empty.setWordWrap(True)
            self.list_layout.insertWidget(0, empty)
            return
        for release in shown:
            self.list_layout.insertWidget(
                self.list_layout.count() - 1, self._build_card(release)
            )

    def _clear_list(self) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # ---------- 卡片 ----------

    def _build_card(self, release: PluginRelease) -> QWidget:
        info = self.manager.get(release.id)
        installed = info is not None and info.status != "清单无效"
        newer = False
        if installed:
            try:
                newer = is_newer(release.version, info.version)
            except Exception:
                newer = False

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

        badges: list[tuple[str, str]] = []
        if release.featured:
            badges.append(("推荐", "#e0b45b"))
        if release.bundled:
            badges.append(("示例", MUTED))
        if installed:
            badges.append(
                (f"已安装 v{info.version}", "#e0b45b" if newer else "#8ed6a2")
            )
        if newer:
            badges.append(("有更新", "#e0b45b"))
        badge_html = "".join(
            f" <span style='color:{color};font-size:10px;'>[{html.escape(label)}]</span>"
            for label, color in badges
        )
        name = QLabel(
            f"<span style='font-size:13px;font-weight:600;'>"
            f"{html.escape(release.name)}</span>"
            f" <span style='color:{MUTED};font-size:10px;'>v{html.escape(release.version)}</span>"
            f"{badge_html}"
        )
        name.setTextFormat(Qt.TextFormat.RichText)
        name.setWordWrap(True)
        name.setStyleSheet(f"color: {TEXT}; background: transparent;")
        row.addWidget(name)

        meta = QLabel(
            f"{release.id} · {release.author or '未署名'} · {format_size(release.size)}"
        )
        meta.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        row.addWidget(meta)

        if release.description:
            desc = QLabel(release.description)
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color: {TEXT}; font-size: 11px; background: transparent;")
            row.addWidget(desc)

        labels = [
            PERMISSION_LABELS.get(item, item) for item in release.permissions
        ]
        perms = QLabel(f"权限：{'、'.join(labels) if labels else '无特殊权限'}")
        perms.setWordWrap(True)
        perms.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        row.addWidget(perms)

        if release.requires:
            parts = []
            for entry in release.requires:
                text = entry.get("name") or "额外组件"
                if entry.get("version"):
                    text += f" {entry['version']}"
                if entry.get("note"):
                    text += f"（{entry['note']}）"
                parts.append(text)
            requires = QLabel("依赖：" + "；".join(parts))
            requires.setWordWrap(True)
            requires.setStyleSheet(
                "color: #e0b45b; font-size: 10px; background: transparent;"
            )
            row.addWidget(requires)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        note_text = ""
        if newer:
            note_text = f"可更新到 v{release.version}"
        elif installed and not info.enabled:
            note_text = "已下载，尚未启用"
        note = QLabel(note_text)
        note.setStyleSheet(f"color: {MUTED}; font-size: 10px; background: transparent;")
        button = QPushButton()
        button.setFixedHeight(26)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        installing = self.manager.installing_plugin == release.id
        if installing:
            button.setText("安装中…")
            button.setEnabled(False)
        elif not installed or newer:
            button.setText(
                f"更新到 v{release.version}" if installed else "下载安装"
            )
            button.setObjectName("primary")
            button.clicked.connect(
                lambda checked=False, pid=release.id: self._install(pid)
            )
        elif info.enabled:
            button.setText("已启用")
            button.setEnabled(False)
            button.setObjectName("ghost")
        else:
            button.setText("启用")
            button.setObjectName("primary")
            button.clicked.connect(
                lambda checked=False, pid=release.id: self._enable(pid)
            )
        if button.objectName() != "primary":
            button.setStyleSheet(
                f"QPushButton {{ color: {MUTED}; background: transparent; "
                f"border: 1px solid {BORDER}; border-radius: 8px; padding: 4px 12px; }}"
            )
        self._buttons[release.id] = button
        actions.addWidget(note, 1)
        actions.addWidget(button)
        row.addLayout(actions)
        return card

    # ---------- 操作 ----------

    def _install(self, plugin_id: str) -> None:
        if self.manager.loading_marketplace:
            self.status.setText("正在刷新插件库，请稍候再安装。")
            return
        button = self._buttons.get(plugin_id)
        if not self.manager.install_from_market(plugin_id):
            self.status.setText("另一个安装任务正在进行，请稍候。")
            return
        if button is not None:
            button.setEnabled(False)
            button.setText("准备下载…")
        self.status.setText(f"正在下载 {plugin_id}…")

    def _enable(self, plugin_id: str) -> None:
        if self.manager.set_enabled(plugin_id, True):
            self.status.setText(f"{plugin_id} 已启用。")
        else:
            self.status.setText(f"{plugin_id} 启用失败，请查看插件日志。")
        self.refresh()

    def _on_progress(self, plugin_id: str, percent: int, message: str) -> None:
        button = self._buttons.get(plugin_id)
        if button is not None:
            if percent >= 100:
                button.setText("正在安装…")
            else:
                button.setText(f"下载中 {max(0, percent)}%")
        self.status.setText(f"{plugin_id}：{message}")

    def _on_installed(self, plugin_id: str, error: str) -> None:
        if error:
            self.status.setText(f"{plugin_id} 安装失败：{error}")
        else:
            info = self.manager.get(plugin_id)
            name = info.name if info is not None else plugin_id
            self.status.setText(f"{name} 已安装。点“启用”后才会运行。")
        self.refresh()

    def _open_site(self) -> None:
        url = self.manager.registry_url()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            self.status.setText("当前插件库不是网页地址，无法打开网页版。")
            return
        QDesktopServices.openUrl(QUrl(urllib.parse.urljoin(url, "plugins.html")))

    # ---------- 窗口 ----------

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
        if not self._positioned:
            self._positioned = True
            self._center_on_screen()
        self.refresh()
        self._ensure_loaded()
