"""灵感获取（QQ桥接 / 桌宠对话）：从记忆和对话里提炼插件灵感，也可以让 AI 按主题生成。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .extractor import (
    PET_SYSTEM_PROMPT,
    QQ_SYSTEM_PROMPT,
    build_generation_messages,
    build_memory_messages,
    parse_ideas,
)
from .inspiration_store import InspirationStore
from .memory_reader import (
    PET_SOURCE,
    QQ_SOURCE,
    clear_captured,
    clear_imports,
    gather_pet_items,
    load_qq_memories,
    record_capture,
    store_imported_text,
)

_api = None
_store = None
_dialog = None
_busy = False


def _data_dir() -> str:
    """插件目录是 data/plugins/<id>，从包内文件往上四级就是 data。"""
    here = os.path.dirname(os.path.abspath(__file__))
    plugin_dir = os.path.dirname(here)
    plugins_dir = os.path.dirname(plugin_dir)
    return os.path.dirname(plugins_dir)


def _settings() -> dict:
    try:
        values = _api.settings if _api is not None else {}
    except Exception:
        values = {}
    return values if isinstance(values, dict) else {}


def _int_setting(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(_settings().get(name, default))
    except Exception:
        value = default
    return max(low, min(high, value))


def _capture_enabled() -> bool:
    return bool(_settings().get("capture_pet_chat", True))


def setup(api):
    global _api, _store
    _api = api
    _store = InspirationStore(api)

    def on_chat_reply(reply: str = "", **kwargs):
        text = reply or kwargs.get("text") or ""
        if _capture_enabled():
            record_capture(api, str(text), "chat")

    def on_screen_comment(text: str = "", **kwargs):
        if _capture_enabled():
            record_capture(api, str(text), "screen")

    try:
        api.on("chat.reply", on_chat_reply)
        api.on("screen.comment", on_screen_comment)
    except Exception as exc:
        api.log(f"订阅桌宠对话事件失败：{exc}")

    api.log(f"灵感获取已启动，当前灵感 {len(_store.all())} 条")
    api.on("startup", lambda: api.log("灵感获取就绪"))


def teardown():
    global _dialog
    _dialog = None


def _open(window) -> bool:
    return window is not None and not getattr(window, "_closed", False)


def _source_prefix(source: str) -> str:
    if source == QQ_SOURCE:
        return "QQ"
    if source == PET_SOURCE:
        return "对话"
    if source == "AI 生成":
        return "AI"
    if source == "导入对话":
        return "导入"
    return "灵感"


def _refresh_list(window) -> None:
    if not _open(window):
        return
    current = window.list_widget.currentItem()
    selected = current.data(Qt.UserRole) if current is not None else None
    window.list_widget.blockSignals(True)
    window.list_widget.clear()
    selected_row = -1
    for row, item in enumerate(_store.all()):
        title = item.get("title") or str(item.get("idea", ""))[:12]
        label = f"[{_source_prefix(str(item.get('source') or ''))}] {title}"
        entry = QListWidgetItem(label)
        entry.setData(Qt.UserRole, item.get("id"))
        window.list_widget.addItem(entry)
        if item.get("id") == selected:
            selected_row = row
    if selected_row >= 0:
        window.list_widget.setCurrentRow(selected_row)
    elif window.list_widget.count():
        window.list_widget.setCurrentRow(0)
    window.list_widget.blockSignals(False)

    current = window.list_widget.currentItem()
    if current is not None:
        _show_detail(window, current.data(Qt.UserRole))
    else:
        window.detail.setPlainText("")
    _update_delete_button(window)

    counts = _store.source_counts()
    parts = [f"共 {len(_store.all())} 条"]
    for source in (QQ_SOURCE, PET_SOURCE, "AI 生成", "导入对话"):
        if counts.get(source):
            parts.append(f"{_source_prefix(source)} {counts[source]}")
    window.count_label.setText(" · ".join(parts))
    if not _store.all():
        window.detail.setPlainText("还没有灵感。\n\n可以从 QQ 桥接提炼、从桌宠对话提炼，或者让 AI 直接生成。")


def _show_detail(window, item_id) -> None:
    item = next((entry for entry in _store.all() if entry.get("id") == item_id), None)
    if not item:
        window.detail.setPlainText("")
        return
    tags = "、".join(item.get("tags") or []) or "无"
    window.detail.setPlainText(
        f"标题：{item.get('title', '')}\n"
        f"来源：{item.get('source', '') or '未标注'}\n"
        f"标签：{tags}\n"
        f"创建：{item.get('created', '')}\n\n"
        f"{item.get('idea', '')}"
    )


def _set_busy(window, busy: bool, message: str = "") -> None:
    for button in getattr(window, "action_buttons", []):
        button.setEnabled(not busy)
    if message and _open(window):
        window.status_label.setText(message)


def _add_ideas(ideas: list[dict], source: str) -> tuple[int, int]:
    added = 0
    skipped = 0
    for idea in ideas:
        if _store.has_idea(idea.get("idea", "")):
            skipped += 1
            continue
        if _store.add(idea.get("title", ""), idea.get("idea", ""), source=source, tags=idea.get("tags")):
            added += 1
    return added, skipped


def _finish_message(window, added: int, skipped: int, prefix: str) -> None:
    parts = [f"{prefix}：新增 {added} 条灵感"]
    if skipped:
        parts.append(f"跳过重复 {skipped} 条")
    if not added:
        parts.append("这次没有新的点子，换个来源或主题再试试")
    window.status_label.setText("，".join(parts))
    if added and _api is not None:
        try:
            _api.say(f"挖到 {added} 条新灵感，去灵感库看看～", 4000)
        except Exception:
            pass


def _run_batches(
    window,
    items: list[dict],
    *,
    source: str,
    system_prompt: str,
    intro: str,
    ask: str,
    batch_size: int,
    per_item_chars: int = 120,
    detail: str = "",
) -> None:
    global _busy
    if _busy or not items:
        return
    _busy = True
    _set_busy(window, True, f"正在准备提炼…{(' · ' + detail) if detail else ''}")

    batches = [items[index : index + batch_size] for index in range(0, len(items), batch_size)]
    state = {"index": 0, "added": 0, "skipped": 0}

    def finish() -> None:
        global _busy
        _busy = False
        if not _open(window):
            window.deleteLater()
            return
        _set_busy(window, False)
        _refresh_list(window)
        _finish_message(window, state["added"], state["skipped"], "提炼完成")

    def run_next() -> None:
        global _busy
        if not _open(window):
            _busy = False
            return
        if state["index"] >= len(batches):
            finish()
            return
        batch = batches[state["index"]]
        state["index"] += 1
        window.status_label.setText(
            f"正在提炼第 {state['index']}/{len(batches)} 批（共 {len(items)} 条）…"
        )

        def on_reply(reply, error):
            global _busy
            if not _open(window):
                _busy = False
                return
            if error:
                _busy = False
                _set_busy(window, False)
                _refresh_list(window)
                window.status_label.setText(
                    f"AI 出错：{error}"
                    + (f"（已保留新增 {state['added']} 条）" if state["added"] else "")
                )
                return
            added, skipped = _add_ideas(parse_ideas(reply or ""), source)
            state["added"] += added
            state["skipped"] += skipped
            run_next()

        try:
            _api.chat(
                build_memory_messages(
                    batch,
                    system_prompt=system_prompt,
                    intro=intro,
                    ask=ask,
                    per_item_chars=per_item_chars,
                    max_items=batch_size,
                ),
                on_reply,
            )
        except Exception as exc:
            _busy = False
            _set_busy(window, False)
            _refresh_list(window)
            window.status_label.setText(f"没法调用 AI：{exc}")

    run_next()


def _extract_qq(window) -> None:
    if _busy:
        return
    items, scanned = load_qq_memories(_data_dir())
    if not items:
        QMessageBox.information(
            window,
            "没找到 QQ 记忆",
            "没有在 QQ 桥接的数据里找到记忆条目。\n\n"
            "确认 QQ 桥接插件已经产生过记忆；也可以先用「从桌宠对话提炼」或「AI 直接生成」。\n\n"
            "查找过的位置：\n" + "\n".join(scanned[:8] or ["（没有可读取的文件）"]),
        )
        return
    limit = _int_setting("max_items_per_run", 120, 12, 500)
    total = len(items)
    if len(items) > limit:
        items = items[-limit:]
    batch_size = _int_setting("max_memories_per_batch", 12, 4, 12)
    _run_batches(
        window,
        items,
        source=QQ_SOURCE,
        system_prompt=QQ_SYSTEM_PROMPT,
        intro="以下是 QQ 桥接记忆：",
        ask="请提炼插件灵感。",
        batch_size=batch_size,
        per_item_chars=120,
        detail=f"QQ 记忆 {total} 条，本次 {len(items)} 条",
    )


def _extract_pet(window) -> None:
    if _busy:
        return
    limit = _int_setting("max_items_per_run", 120, 12, 500)
    items, details = gather_pet_items(_api, _data_dir(), limit=min(limit, 120))
    if not items:
        QMessageBox.information(
            window,
            "还没有桌宠对话",
            "当前没有可用的桌宠对话。可以先做任意一种：\n\n"
            "· 打开聊天框和桌宠聊几句，再回来提炼；\n"
            "· 点「导入对话…」粘贴一段聊天记录；\n"
            "· 让桌宠在屏幕上说几句话，运行中会自动抓取。",
        )
        return
    if len(items) > limit:
        items = items[:limit]
    batch_size = _int_setting("max_pet_items_per_batch", 6, 3, 10)
    detail = (
        f"当前会话 {details.get('live', 0)}、抓取 {details.get('captured', 0)}、"
        f"导入 {details.get('imported', 0)}、内核历史 {details.get('alive', 0)}"
    )
    _run_batches(
        window,
        items,
        source=PET_SOURCE,
        system_prompt=PET_SYSTEM_PROMPT,
        intro="以下是桌宠和主人之间的对话片段：",
        ask="请从这些对话里提炼插件灵感。",
        batch_size=batch_size,
        per_item_chars=480,
        detail=detail,
    )


def _generate_dialog(window) -> None:
    dialog = QDialog(window)
    dialog.setWindowTitle("AI 直接生成灵感")
    dialog.resize(460, 220)

    root = QVBoxLayout(dialog)
    hint = QLabel(
        "给一个主题，AI 会主动发明适合桌宠的小插件。\n"
        "留空就随机发挥；生成结果会直接进入灵感库。"
    )
    hint.setWordWrap(True)
    root.addWidget(hint)

    form = QFormLayout()
    theme = QLineEdit()
    theme.setPlaceholderText("例如：猫娘、学习陪伴、桌面整理、下雨天")
    form.addRow("主题", theme)
    count = QSpinBox()
    count.setRange(1, 3)
    count.setValue(3)
    form.addRow("数量", count)
    root.addLayout(form)
    root.addStretch(1)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(cancel)
    create = QPushButton("生成")
    create.setDefault(True)
    create.clicked.connect(dialog.accept)
    buttons.addWidget(create)
    root.addLayout(buttons)

    if dialog.exec() != QDialog.Accepted:
        return
    _run_generate(window, theme.text().strip(), count.value())


def _run_generate(window, theme: str, count: int) -> None:
    global _busy
    if _busy:
        return
    _busy = True
    _set_busy(window, True, "AI 正在想新点子…")
    existing = [str(item.get("title") or "") for item in _store.all()][:15]
    messages = build_generation_messages(theme, count, existing)

    def on_reply(reply, error):
        global _busy
        if not _open(window):
            _busy = False
            return
        _busy = False
        _set_busy(window, False)
        if error:
            window.status_label.setText(f"AI 出错：{error}")
            return
        ideas = parse_ideas(reply or "")
        if not ideas:
            window.status_label.setText("AI 这次没给出可用灵感，换个主题再试试")
            return
        added, skipped = _add_ideas(ideas, "AI 生成")
        _refresh_list(window)
        _finish_message(window, added, skipped, "生成完成")

    try:
        _api.chat(messages, on_reply)
    except Exception as exc:
        _busy = False
        _set_busy(window, False)
        window.status_label.setText(f"没法调用 AI：{exc}")


def _import_dialog(window) -> None:
    dialog = QDialog(window)
    dialog.setWindowTitle("导入聊天记录")
    dialog.resize(620, 440)

    root = QVBoxLayout(dialog)
    hint = QLabel(
        "把想提炼的聊天记录贴进来，一行一句效果最好，例如：\n"
        "主人：今天好累\n桌宠：那要不要我陪你发会儿呆？\n\n"
        "内容只保存在本插件的本地 storage 里，不会上传到别处。"
    )
    hint.setWordWrap(True)
    root.addWidget(hint)

    editor = QPlainTextEdit()
    editor.setPlaceholderText("粘贴聊天记录…")
    root.addWidget(editor, 1)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    cancel = QPushButton("取消")
    cancel.clicked.connect(dialog.reject)
    buttons.addWidget(cancel)
    save = QPushButton("保存并提炼")
    save.setDefault(True)
    save.clicked.connect(dialog.accept)
    buttons.addWidget(save)
    root.addLayout(buttons)

    if dialog.exec() != QDialog.Accepted:
        return
    count = store_imported_text(_api, editor.toPlainText())
    if not count:
        QMessageBox.information(window, "没有内容", "没有识别到可用的聊天内容。")
        return
    window.status_label.setText(f"已保存 {count} 段对话，开始提炼…")
    _extract_pet(window)


def _clear_dialog(window) -> None:
    box = QMessageBox(window)
    box.setWindowTitle("清理记录")
    box.setText(
        "只清理本插件自己保存的实时抓取和手动导入内容，"
        "不会改动 QQ 桥接、活人感内核或灵感库。"
    )
    captured_btn = box.addButton("清空实时抓取", QMessageBox.AcceptRole)
    imported_btn = box.addButton("清空手动导入", QMessageBox.ActionRole)
    box.addButton("取消", QMessageBox.RejectRole)
    box.exec()

    clicked = box.clickedButton()
    if clicked is captured_btn:
        count = clear_captured(_api)
        window.status_label.setText(f"已清空 {count} 条实时抓取记录")
    elif clicked is imported_btn:
        count = clear_imports(_api)
        window.status_label.setText(f"已清空 {count} 段手动导入记录")


def _diagnose(window) -> None:
    qq_items, qq_files = load_qq_memories(_data_dir())
    pet_items, details = gather_pet_items(_api, _data_dir(), limit=120)
    counts = _store.source_counts()

    lines = [
        f"数据目录：{_data_dir()}",
        f"QQ 桥接：{len(qq_items)} 条记忆（读取 {len(qq_files)} 个文件）",
        "桌宠对话："
        f"{len(pet_items)} 条 = 当前会话 {details.get('live', 0)}"
        f" + 实时抓取 {details.get('captured', 0)}"
        f" + 手动导入 {details.get('imported', 0)}"
        f" + 内核历史 {details.get('alive', 0)}",
        f"灵感库：{len(_store.all())} 条",
    ]
    if counts:
        lines.append("按来源：" + "、".join(f"{key} {value}" for key, value in counts.items()))
    lines.append("")
    lines.append("隐私：只读 memory.json，跳过 config/settings/credentials/secrets。")
    if qq_files:
        lines.append("读取的文件：")
        lines.extend("  " + path for path in qq_files[:8])

    QMessageBox.information(window, "诊断", "\n".join(lines))


def _copy_to_workshop(window) -> None:
    current = window.list_widget.currentItem()
    if not current:
        QMessageBox.information(window, "提示", "先在左边选一条灵感。")
        return
    item_id = current.data(Qt.UserRole)
    item = next((entry for entry in _store.all() if entry.get("id") == item_id), None)
    if not item:
        return
    tags = "、".join(item.get("tags") or []) or "无"
    text = (
        f"【插件灵感】{item.get('title', '')}\n"
        f"{item.get('idea', '')}\n"
        f"来源：{item.get('source', '') or '未标注'}\n"
        f"标签：{tags}"
    )
    QGuiApplication.clipboard().setText(text)
    window.status_label.setText("已复制到剪贴板，去自开发工坊粘贴即可。")
    try:
        _api.say("灵感已复制，去工坊里粘贴吧～", 4000)
    except Exception:
        pass


def _selected_ids(window) -> list[str]:
    ids = []
    for item in window.list_widget.selectedItems():
        item_id = item.data(Qt.UserRole)
        if item_id:
            ids.append(str(item_id))
    return ids


def _update_delete_button(window) -> None:
    button = getattr(window, "delete_button", None)
    if button is None:
        return
    count = len(_selected_ids(window))
    button.setText(f"一键删除（{count}）" if count else "一键删除")
    button.setEnabled(count > 0)


def _delete_selected(window) -> None:
    ids = _selected_ids(window)
    if not ids:
        window.status_label.setText("先在左边选中要删除的灵感，可按住 Ctrl 或 Shift 多选")
        return
    removed = _store.remove_many(ids)
    _refresh_list(window)
    window.status_label.setText(f"已删除 {removed} 条灵感" if removed else "没有删除任何灵感")


def _show_list_menu(window, pos) -> None:
    item = window.list_widget.itemAt(pos)
    if item is None:
        return
    if not item.isSelected():
        window.list_widget.clearSelection()
        window.list_widget.setCurrentItem(item)
        item.setSelected(True)
    count = len(_selected_ids(window))
    menu = QMenu(window)
    action = menu.addAction(f"删除选中（{count} 条）" if count > 1 else "删除这条")
    action.triggered.connect(lambda: _delete_selected(window))
    menu.exec(window.list_widget.viewport().mapToGlobal(pos))


def _on_dialog_closed(dialog) -> None:
    global _dialog, _busy
    dialog._closed = True
    if _dialog is dialog:
        _dialog = None
    _busy = False
    dialog.deleteLater()


def open_settings(api, parent=None):
    global _dialog
    if _dialog is not None and _open(_dialog):
        try:
            _dialog.raise_()
            _dialog.activateWindow()
            return _dialog
        except RuntimeError:
            _dialog = None

    dialog = QDialog(parent)
    dialog.setWindowTitle("灵感获取（QQ桥接 / 桌宠对话）")
    dialog.resize(920, 640)
    dialog.action_buttons = []

    root = QVBoxLayout(dialog)

    top = QHBoxLayout()
    dialog.count_label = QLabel("")
    top.addWidget(dialog.count_label)
    top.addStretch(1)
    for text, handler in (
        ("从 QQ 桥接提炼", lambda: _extract_qq(dialog)),
        ("从桌宠对话提炼", lambda: _extract_pet(dialog)),
        ("AI 直接生成", lambda: _generate_dialog(dialog)),
    ):
        button = QPushButton(text)
        button.clicked.connect(handler)
        top.addWidget(button)
        dialog.action_buttons.append(button)
    root.addLayout(top)

    tools = QHBoxLayout()
    tools.addStretch(1)
    for text, handler in (
        ("导入对话…", lambda: _import_dialog(dialog)),
        ("清理记录…", lambda: _clear_dialog(dialog)),
        ("诊断", lambda: _diagnose(dialog)),
    ):
        button = QPushButton(text)
        button.clicked.connect(handler)
        tools.addWidget(button)
        dialog.action_buttons.append(button)
    root.addLayout(tools)

    splitter = QSplitter(Qt.Horizontal)
    dialog.list_widget = QListWidget()
    dialog.list_widget.setSelectionMode(QAbstractItemView.ExtendedSelection)
    dialog.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
    dialog.list_widget.customContextMenuRequested.connect(
        lambda pos: _show_list_menu(dialog, pos)
    )
    dialog.list_widget.currentItemChanged.connect(
        lambda current, _previous: _show_detail(dialog, current.data(Qt.UserRole)) if current else None
    )
    dialog.list_widget.itemSelectionChanged.connect(lambda: _update_delete_button(dialog))
    splitter.addWidget(dialog.list_widget)

    right = QWidget()
    right_layout = QVBoxLayout(right)
    dialog.detail = QTextEdit()
    dialog.detail.setReadOnly(True)
    right_layout.addWidget(dialog.detail, 1)
    bottom = QHBoxLayout()
    copy_btn = QPushButton("复制到工坊")
    copy_btn.clicked.connect(lambda: _copy_to_workshop(dialog))
    bottom.addWidget(copy_btn)
    dialog.delete_button = QPushButton("一键删除")
    dialog.delete_button.setToolTip("删除当前选中的灵感；按住 Ctrl 或 Shift 可以多选，点一次全部删除")
    dialog.delete_button.clicked.connect(lambda: _delete_selected(dialog))
    bottom.addWidget(dialog.delete_button)
    bottom.addStretch(1)
    right_layout.addLayout(bottom)
    splitter.addWidget(right)
    splitter.setSizes([280, 640])
    root.addWidget(splitter, 1)

    dialog.status_label = QLabel("就绪 · 按住 Ctrl 或 Shift 可多选，Delete 键也能一键删除")
    dialog.status_label.setWordWrap(True)
    root.addWidget(dialog.status_label)

    delete_shortcut = QShortcut(QKeySequence(Qt.Key_Delete), dialog)
    delete_shortcut.activated.connect(lambda: _delete_selected(dialog))

    dialog.finished.connect(lambda _result: _on_dialog_closed(dialog))
    _refresh_list(dialog)
    _dialog = dialog
    return dialog
