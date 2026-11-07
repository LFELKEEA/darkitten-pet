"""插件设置窗口。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)


class DeepSeekChanDialog(QDialog):
    def __init__(self, service, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.api = service.api
        self.setWindowTitle("深度求索娘 · 桌宠皮肤")
        self.setMinimumWidth(520)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QVBoxLayout(self)
        root.setSpacing(12)

        title = QLabel("深度求索娘（蓝色大肥鱼）")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        root.addWidget(title)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        model_box = QGroupBox("模型")
        model_layout = QVBoxLayout(model_box)
        self.model_label = QLabel("")
        self.model_label.setWordWrap(True)
        self.model_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        model_layout.addWidget(self.model_label)
        buttons = QHBoxLayout()
        self.pick_model = QPushButton("选择 .model3.json")
        self.pick_zip = QPushButton("选择 ZIP")
        self.rescan = QPushButton("重新扫描")
        buttons.addWidget(self.pick_model)
        buttons.addWidget(self.pick_zip)
        buttons.addWidget(self.rescan)
        buttons.addStretch(1)
        model_layout.addLayout(buttons)
        root.addWidget(model_box)

        behavior_box = QGroupBox("行为")
        behavior_layout = QFormLayout(behavior_box)
        self.idle_check = QCheckBox("待机时随机做小动作")
        self.chatter_check = QCheckBox("偶尔主动说话")
        self.reaction_check = QCheckBox("摸头 / 戳一戳 / 聊天时变表情")
        self.persona_check = QCheckBox("把 AI 说话风格换成深度求索娘")
        self.gaze_check = QCheckBox("视线跟随鼠标")
        behavior_layout.addRow(self.idle_check)
        behavior_layout.addRow(self.chatter_check)
        behavior_layout.addRow(self.reaction_check)
        behavior_layout.addRow(self.persona_check)
        behavior_layout.addRow(self.gaze_check)

        intervals = QGridLayout()
        intervals.addWidget(QLabel("小动作间隔（秒）"), 0, 0)
        self.idle_min = QSpinBox()
        self.idle_min.setRange(4, 300)
        self.idle_max = QSpinBox()
        self.idle_max.setRange(4, 600)
        intervals.addWidget(self.idle_min, 0, 1)
        intervals.addWidget(QLabel("到"), 0, 2)
        intervals.addWidget(self.idle_max, 0, 3)
        intervals.addWidget(QLabel("主动说话间隔（分钟）"), 1, 0)
        self.chatter_min = QSpinBox()
        self.chatter_min.setRange(1, 240)
        self.chatter_max = QSpinBox()
        self.chatter_max.setRange(1, 480)
        intervals.addWidget(self.chatter_min, 1, 1)
        intervals.addWidget(QLabel("到"), 1, 2)
        intervals.addWidget(self.chatter_max, 1, 3)
        behavior_layout.addRow(intervals)
        root.addWidget(behavior_box)

        preview_box = QGroupBox("试一下")
        preview_layout = QHBoxLayout(preview_box)
        self.action_combo = QComboBox()
        self.action_combo.setMinimumWidth(160)
        self.expression_combo = QComboBox()
        self.expression_combo.setMinimumWidth(120)
        play_action = QPushButton("播放动作")
        play_expression = QPushButton("设置表情")
        clear_expression = QPushButton("恢复表情")
        preview_layout.addWidget(self.action_combo)
        preview_layout.addWidget(play_action)
        preview_layout.addWidget(self.expression_combo)
        preview_layout.addWidget(play_expression)
        preview_layout.addWidget(clear_expression)
        root.addWidget(preview_box)

        credit = QLabel(service.credits())
        credit.setWordWrap(True)
        credit.setStyleSheet("color: #777; font-size: 12px;")
        root.addWidget(credit)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.save_button = QPushButton("保存")
        self.close_button = QPushButton("关闭")
        footer.addWidget(self.save_button)
        footer.addWidget(self.close_button)
        root.addLayout(footer)

        self.pick_model.clicked.connect(self._pick_model)
        self.pick_zip.clicked.connect(self._pick_zip)
        self.rescan.clicked.connect(self._rescan)
        self.save_button.clicked.connect(self._save)
        self.close_button.clicked.connect(self.close)
        play_action.clicked.connect(self._play_action)
        play_expression.clicked.connect(self._play_expression)
        clear_expression.clicked.connect(self._clear_expression)

        self._load_values()
        self._refresh_status()

    def _load_values(self) -> None:
        settings = self.service.settings
        self.idle_check.setChecked(bool(settings.get("idle_actions", True)))
        self.chatter_check.setChecked(bool(settings.get("chatter", True)))
        self.reaction_check.setChecked(bool(settings.get("reactions", True)))
        self.persona_check.setChecked(bool(settings.get("persona", True)))
        self.gaze_check.setChecked(bool(settings.get("gaze_follow", True)))
        self.idle_min.setValue(int(settings.get("idle_min_seconds", 10) or 10))
        self.idle_max.setValue(int(settings.get("idle_max_seconds", 24) or 24))
        self.chatter_min.setValue(int(settings.get("chatter_min_minutes", 4) or 4))
        self.chatter_max.setValue(int(settings.get("chatter_max_minutes", 9) or 9))
        self.action_combo.clear()
        self.action_combo.addItems(self.service.available_actions())
        self.expression_combo.clear()
        self.expression_combo.addItems(self.service.available_expressions())

    def _refresh_status(self) -> None:
        model = self.service.model_path
        if self.service.ready and model is not None:
            self.status.setText("模型运行中")
            self.model_label.setText(str(model))
        elif model is not None:
            self.status.setText("模型加载中…")
            self.model_label.setText(str(model))
        else:
            self.status.setText(
                "还没找到模型。插件不带模型：去 B 站神宫凉子（UID 13737731）下载 "
                "DeepSeek免费公皮.zip，放到 D 盘根目录或下载目录，或者手动选择。"
            )
            self.model_label.setText("未加载")

    def _pick_model(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Live2D 模型", str(Path.home()), "Live2D 模型 (*.model3.json)"
        )
        if path:
            self._apply_model(path)

    def _pick_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模型压缩包", str(Path.home()), "压缩包 (*.zip)"
        )
        if path:
            self._apply_model(path)

    def _apply_model(self, path: str) -> None:
        self.service.settings["model_path"] = path
        self.api.save_settings({"model_path": path})
        self.service.reload_model(path)
        self._refresh_status()

    def _rescan(self) -> None:
        self.service.settings["model_path"] = ""
        self.api.save_settings({"model_path": ""})
        self.service.reload_model()
        self._refresh_status()

    def _save(self) -> None:
        low = min(self.idle_min.value(), self.idle_max.value())
        high = max(self.idle_min.value(), self.idle_max.value())
        c_low = min(self.chatter_min.value(), self.chatter_max.value())
        c_high = max(self.chatter_min.value(), self.chatter_max.value())
        values = {
            "idle_actions": self.idle_check.isChecked(),
            "chatter": self.chatter_check.isChecked(),
            "reactions": self.reaction_check.isChecked(),
            "persona": self.persona_check.isChecked(),
            "gaze_follow": self.gaze_check.isChecked(),
            "idle_min_seconds": low,
            "idle_max_seconds": high,
            "chatter_min_minutes": c_low,
            "chatter_max_minutes": c_high,
        }
        self.service.settings.update(values)
        self.api.save_settings(values)
        try:
            self.api.model.follow_cursor(self.gaze_check.isChecked())
        except Exception:
            pass
        self.status.setText("设置已保存")
        self._refresh_status()

    def _play_action(self) -> None:
        name = self.action_combo.currentText()
        if name:
            self.service.preview_action(name)

    def _play_expression(self) -> None:
        name = self.expression_combo.currentText()
        if name:
            self.service._expression(name, hold=4.0)

    def _clear_expression(self) -> None:
        try:
            self.api.model.clear_expression()
        except Exception:
            pass
