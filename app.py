from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app_paths import ASSET_DIR
from chat import ChatWindow
from config import Config
from conversation import Conversation
from local_replies import greeting
from pet import PetWindow
from plugin_manager import PluginManager
from plugins_ui import PluginDialog
from screen_watcher import ScreenWatcher
from settings import SettingsDialog
from system_utils import set_startup_enabled
from theme import MENU_STYLE
from update_ui import UpdateDialog, UpdateManager
from version import APP_VERSION
from voice import VoiceChatController


class PetApp(QObject):
    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        self.config = Config()
        self.conversation = Conversation(24, self)
        self.pet = PetWindow(self.config)
        self.chat: ChatWindow | None = None
        self.settings_dialog: SettingsDialog | None = None
        self.plugin_dialog: PluginDialog | None = None
        self.update_dialog: UpdateDialog | None = None
        self.update_manager = UpdateManager(self)
        self.plugins = PluginManager(self.config, self.pet, self.conversation, self)
        self.screen = ScreenWatcher(
            self.config, self.pet, self.conversation, self.plugins, self
        )
        self.voice = VoiceChatController(
            self.config, self.pet, self, conversation=self.conversation
        )

        self._cleaned = False
        self.pet.chat_requested.connect(self.open_chat)
        self.pet.settings_requested.connect(self.open_settings)
        self.pet.quit_requested.connect(self.quit)
        self.pet.visibility_changed.connect(self._on_pet_visibility)
        self.pet.always_on_top_changed.connect(self._on_top_changed)
        self.pet.scale_changed.connect(self._on_scale_changed)
        self.pet.voice_primed.connect(self.voice.prime)
        self.pet.voice_started.connect(self.voice.start_listening)
        self.pet.voice_finished.connect(self.voice.finish_listening)
        self.pet.voice_cancelled.connect(self.voice.cancel_listening)
        self.pet.observe_screen_requested.connect(self.observe_screen)
        self.pet.plugins_requested.connect(self.open_plugins)
        self.pet.update_requested.connect(lambda: self.check_updates(manual=True))
        self.pet.screen_watch_toggled.connect(self.set_screen_watch)
        self.pet.petted.connect(lambda: self.plugins.emit("pet.pat"))
        self.pet.poked.connect(lambda: self.plugins.emit("pet.poke"))
        self.screen.status_changed.connect(self._on_screen_status)
        self.screen.spoke.connect(self._on_screen_spoke)

        self.tray = QSystemTrayIcon(self.pet.icon(), self)
        self.tray.setToolTip("LFELKEEA · 长按我说话")
        self.tray_menu = QMenu()
        self.tray_menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.tray_menu.setStyleSheet(MENU_STYLE)
        self.show_action = QAction("隐藏 LFELKEEA", self)
        self.show_action.triggered.connect(self.pet.toggle_visible)
        self.chat_action = QAction("和 LFELKEEA 聊天", self)
        self.chat_action.triggered.connect(self.open_chat)
        self.observe_action = QAction("看看屏幕，说句话", self)
        self.observe_action.triggered.connect(self.observe_screen)
        self.screen_action = QAction("定时看屏幕", self)
        self.screen_action.setCheckable(True)
        self.screen_action.setChecked(self._screen_watch_enabled())
        self.screen_action.triggered.connect(self.set_screen_watch)
        self.plugins_action = QAction("插件管理", self)
        self.plugins_action.triggered.connect(self.open_plugins)
        self.update_action = QAction("检查更新…", self)
        self.update_action.triggered.connect(lambda: self.check_updates(manual=True))
        self.settings_action = QAction("AI 与桌宠设置", self)
        self.settings_action.triggered.connect(self.open_settings)
        self.top_action = QAction("始终置顶", self)
        self.top_action.setCheckable(True)
        self.top_action.setChecked(bool(self.config.get("always_on_top", True)))
        self.top_action.triggered.connect(self._toggle_top)
        self.tray_menu.addAction(self.show_action)
        self.tray_menu.addAction(self.chat_action)
        self.tray_menu.addAction(self.observe_action)
        self.tray_menu.addAction(self.screen_action)
        self.tray_menu.addAction(self.plugins_action)
        self.tray_menu.addAction(self.update_action)

        actions_menu = self.tray_menu.addMenu("动作")
        actions_menu.setStyleSheet(MENU_STYLE)
        for key, label in (
            ("hop", "跳一下"),
            ("spin", "转个圈"),
            ("stretch", "伸个懒腰"),
            ("shake", "抖抖耳朵"),
            ("lean", "歪歪头"),
            ("nod", "点点头"),
        ):
            act = actions_menu.addAction(label)
            act.triggered.connect(lambda checked=False, k=key: self.pet.start_action(k, announce=True))

        size_menu = self.tray_menu.addMenu("显示大小")
        size_menu.setStyleSheet(MENU_STYLE)
        self._size_actions: dict[QAction, int] = {}
        self._size_group = QActionGroup(self)
        self._size_group.setExclusive(True)
        for pct in (70, 85, 100, 115, 130, 145):
            act = size_menu.addAction(f"{pct}%")
            act.setCheckable(True)
            act.setChecked(abs(self.pet.scale_factor * 100 - pct) < 1.0)
            act.triggered.connect(lambda checked=False, p=pct: self.pet.set_scale(p / 100.0))
            self._size_group.addAction(act)
            self._size_actions[act] = pct

        self.tray_menu.addAction(self.settings_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.top_action)
        self.tray_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit)
        self.tray_menu.addAction(quit_action)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        self.app.aboutToQuit.connect(self._cleanup)
        self.pet.show()
        self.update_manager.checked.connect(self._on_update_checked)
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(6 * 60 * 60 * 1000)
        self._update_timer.timeout.connect(self._periodic_check_updates)
        self._update_timer.start()
        QTimer.singleShot(1200, self._start_services)
        QTimer.singleShot(900, self._welcome)
        QTimer.singleShot(9000, self._startup_check_updates)

    def _start_services(self) -> None:
        if self._cleaned:
            return
        self.plugins.load_enabled()
        self.screen.apply_settings()
        if self.settings_dialog is not None:
            self.settings_dialog.set_screen_status(self.screen.last_status)

    # ---------- 自动更新 ----------

    def _update_config(self) -> dict:
        value = self.config.get("updates", {})
        return dict(value) if isinstance(value, dict) else {}

    def _startup_check_updates(self) -> None:
        """启动时总是查一次，避免 6 小时节流把重启后的检查也挡掉。"""
        self._auto_check_updates(startup=True)

    def _periodic_check_updates(self) -> None:
        self._auto_check_updates()

    def _auto_check_updates(self, startup: bool = False) -> None:
        if self._cleaned:
            return
        updates = self._update_config()
        if not bool(updates.get("auto_check", True)):
            return
        try:
            last = int(updates.get("last_check", 0) or 0)
        except (TypeError, ValueError):
            last = 0
        if not startup and time.time() - last < 6 * 60 * 60:
            return
        if self.update_manager.busy:
            return
        self.update_manager.check(manual=False)

    def check_updates(self, manual: bool = True) -> None:
        if self.update_manager.busy:
            if manual:
                self.pet.say("还在检查或下载更新，稍等一下。", 3200)
            return
        if manual:
            self.pet.say("我去看看有没有新版本…", 2600)
            if self.settings_dialog is not None:
                self.settings_dialog.set_update_status("正在检查更新…")
        self.update_manager.check(manual=manual)

    def _on_update_checked(self, release: object, error: str, manual: bool) -> None:
        if self._cleaned:
            return
        updates = self._update_config()
        if not error:
            updates["last_check"] = int(time.time())
            self.config.set("updates", updates, save=True)
        if error:
            if self.settings_dialog is not None:
                self.settings_dialog.set_update_status(f"检查失败：{error}")
            if manual:
                self._show_update_dialog(None, error=error)
            return
        if release is None:
            text = f"已经是最新版本 v{APP_VERSION}"
            if self.settings_dialog is not None:
                self.settings_dialog.set_update_status(text)
            if manual:
                self.pet.say("已经是最新版本啦。", 2800)
                self._show_update_dialog(None)
            return
        ignored = str(updates.get("ignored_version", "") or "")
        if not manual and ignored == release.version:
            return
        if self.settings_dialog is not None:
            self.settings_dialog.set_update_status(f"有新版本 v{release.version}")
        if not self.pet.isVisible():
            self.pet.show_pet()
        self._show_update_dialog(release)
        if not manual:
            self.pet.say(f"有新版本 v{release.version}，要更新吗？", 5200)

    def _show_update_dialog(self, release, error: str = "") -> None:
        if self.update_dialog is not None:
            self.update_dialog.close()
            self.update_dialog = None
        dialog = UpdateDialog(
            self.update_manager,
            release,
            current_version=APP_VERSION,
            error=error,
            parent=self.pet,
        )
        dialog.ignore_requested.connect(self._ignore_update_version)
        dialog.finished.connect(self._update_dialog_closed)
        self.update_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _ignore_update_version(self, version: str) -> None:
        updates = self._update_config()
        updates["ignored_version"] = str(version or "")
        self.config.set("updates", updates, save=True)

    def _update_dialog_closed(self, result: int) -> None:
        self.update_dialog = None

    def _screen_watch_enabled(self) -> bool:
        value = self.config.get("screen_watch", {})
        return bool(value.get("enabled", False)) if isinstance(value, dict) else False

    def observe_screen(self) -> None:
        if not self.pet.isVisible():
            self.pet.show_pet()
        self.screen.observe_now()

    def set_screen_watch(self, enabled: bool) -> None:
        value = self.config.get("screen_watch", {})
        if not isinstance(value, dict):
            value = {}
        value["enabled"] = bool(enabled)
        self.config.set("screen_watch", value, save=True)
        self.screen.apply_settings()
        self.screen_action.setChecked(bool(enabled))
        if self.settings_dialog is not None:
            self.settings_dialog.screen_enable.setChecked(bool(enabled))
            self.settings_dialog.set_screen_status(self.screen.last_status)
        if enabled:
            self.pet.say("好，我会隔一会儿看看屏幕。", 3200)
        else:
            self.pet.say("那我先不看了。", 2600)

    def _on_screen_status(self, text: str) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.set_screen_status(text)

    def _on_screen_spoke(self, text: str) -> None:
        if self.chat is not None and self.chat.isVisible():
            self.chat.append_message("pet", text)

    def _welcome(self) -> None:
        if self.config.get("first_run", True):
            self.pet.say(
                "你好呀，我是 LFELKEEA。长按我说话，松开就能聊；双击打开聊天框；右键有菜单。Ctrl+滚轮可以调大小。",
                8800,
            )
            self.config.set("first_run", False)
            self.config.set("voice_hint_seen", True, save=True)
        else:
            self.pet.say(greeting(), 4600)
            if not self.config.get("voice_hint_seen", False):
                QTimer.singleShot(5400, self._show_voice_hint)

    def _show_voice_hint(self) -> None:
        if self._cleaned or not self.pet.isVisible():
            return
        self.pet.say("长按我不要松手，说完再松开，就能直接语音对话，不用打开聊天框。", 7200)
        self.config.set("voice_hint_seen", True, save=True)

    def _ensure_chat(self) -> ChatWindow:
        if self.chat is None:
            self.chat = ChatWindow(self.config, self.conversation)
            self.chat.reply_ready.connect(self._on_reply)
            self.chat.settings_requested.connect(self.open_settings)
            self.chat.visibility_changed.connect(self.pet.set_chat_open)
        return self.chat

    def open_chat(self) -> None:
        if not self.pet.isVisible():
            self.pet.show_pet()
        chat = self._ensure_chat()
        chat.show_near(self.pet._visible_rect())
        self.pet.set_chat_open(True)

    def _on_reply(self, reply: str) -> None:
        short = reply.strip()
        if len(short) > 100:
            short = short[:100].rstrip() + "……"
        self.pet.say(short, 6000)
        self.pet._trigger_bounce()

    def open_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(self.config, self.pet)
        dialog.clear_chat_requested.connect(self._clear_chat)
        dialog.settings_saved.connect(self._on_settings_saved)
        dialog.observe_now_requested.connect(self.observe_screen)
        dialog.plugins_requested.connect(self.open_plugins)
        dialog.check_updates_requested.connect(lambda: self.check_updates(manual=True))
        dialog.finished.connect(self._settings_closed)
        dialog.set_screen_status(self.screen.last_status)
        self.settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _settings_closed(self, result: int) -> None:
        self.settings_dialog = None

    def open_plugins(self) -> None:
        if self.plugin_dialog is not None:
            self.plugin_dialog.raise_()
            self.plugin_dialog.activateWindow()
            return
        dialog = PluginDialog(self.plugins, self.pet)
        dialog.finished.connect(self._plugins_closed)
        self.plugin_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _plugins_closed(self, result: int) -> None:
        self.plugin_dialog = None

    def _on_settings_saved(self) -> None:
        top = bool(self.config.get("always_on_top", True))
        self.top_action.setChecked(top)
        self.pet.set_always_on_top(top)
        self.pet.apply_chatter_settings()
        self.screen_action.setChecked(self._screen_watch_enabled())
        self.screen.apply_settings()
        if self.chat is not None:
            self.chat.refresh_provider()
        self.pet.say("设置好了。", 2600)

    def _clear_chat(self) -> None:
        if self.chat is not None:
            self.chat.clear_history()

    def _toggle_top(self, checked: bool) -> None:
        self.pet.set_always_on_top(checked)

    def _on_pet_visibility(self, visible: bool) -> None:
        self.show_action.setText("隐藏 LFELKEEA" if visible else "显示 LFELKEEA")

    def _on_top_changed(self, enabled: bool) -> None:
        self.top_action.setChecked(enabled)

    def _on_scale_changed(self, scale: float) -> None:
        percent = round(scale * 100)
        for action, pct in self._size_actions.items():
            action.setChecked(abs(percent - pct) < 1)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_chat()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            if not self.pet.isVisible():
                self.pet.show_pet()

    def quit(self) -> None:
        self._cleanup()
        self.app.quit()

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        self.tray.hide()
        self.screen.shutdown()
        self.plugins.shutdown()
        self.voice.shutdown()
        self.pet.cleanup()
        if self.chat is not None:
            self.chat.hide()
            self.chat.close()
        if self.plugin_dialog is not None:
            self.plugin_dialog.close()
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        if self.update_dialog is not None:
            self.update_dialog.close()
            self.update_dialog = None
        self.config.save()


def run(app: QApplication | None = None) -> int:
    app = app or QApplication.instance() or QApplication([])
    app.setApplicationName("LFELKEEA")
    app.setApplicationVersion(APP_VERSION)
    app.setApplicationDisplayName("LFELKEEA · 桌面宠物")
    app.setOrganizationName("LFELKEEA")
    app.setQuitOnLastWindowClosed(False)
    icon_path = ASSET_DIR / "avatar.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    try:
        from PySide6.QtGui import QFont

        font = QFont("Microsoft YaHei UI", 10)
        app.setFont(font)
    except Exception:
        pass
    controller = PetApp(app)
    app._rin_controller = controller  # type: ignore[attr-defined]
    return app.exec()
