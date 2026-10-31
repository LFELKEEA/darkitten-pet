from __future__ import annotations

from PySide6.QtGui import QColor


BG = "#171214"
CARD = "#21181c"
CARD_2 = "#2b1f24"
BORDER = "#432a31"
ACCENT = "#bf2b35"
ACCENT_LIGHT = "#e0525b"
TEXT = "#f6ecef"
MUTED = "#a58f95"
BUBBLE_BG = QColor(27, 19, 23, 238)
BUBBLE_BORDER = QColor(205, 68, 78, 210)

FONT_FAMILY = "Microsoft YaHei UI"
FONT_FALLBACK = "Microsoft YaHei, Segoe UI, sans-serif"


MENU_STYLE = f"""
QMenu {{
    background-color: {CARD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 6px;
    font-family: "{FONT_FAMILY}";
    font-size: 12px;
}}
QMenu::item {{
    padding: 7px 26px 7px 14px;
    border-radius: 6px;
    margin: 1px 2px;
}}
QMenu::item:selected {{
    background-color: {CARD_2};
    color: #ffffff;
}}
QMenu::item:disabled {{
    color: #6f5c62;
}}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 5px 8px;
}}
QMenu::indicator {{
    width: 13px;
    height: 13px;
}}
"""

DIALOG_STYLE = f"""
QDialog {{
    background: transparent;
}}
QFrame#card {{
    background-color: {CARD};
    border: 1px solid {BORDER};
    border-radius: 16px;
}}
QLabel {{
    color: {TEXT};
    font-family: "{FONT_FAMILY}";
    font-size: 12px;
    background: transparent;
}}
QLabel#title {{
    font-size: 16px;
    font-weight: 600;
}}
QLabel#muted {{
    color: {MUTED};
    font-size: 11px;
}}
QLabel#section {{
    color: {MUTED};
    font-size: 11px;
    letter-spacing: 1px;
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: #1a1316;
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    font-family: "{FONT_FAMILY}";
    font-size: 12px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QComboBox:focus {{
    border: 1px solid {ACCENT_LIGHT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    background: #1a1316;
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    outline: none;
}}
QCheckBox {{
    color: {TEXT};
    font-family: "{FONT_FAMILY}";
    font-size: 12px;
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {BORDER};
    background: #1a1316;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border: 1px solid {ACCENT_LIGHT};
}}
QPushButton {{
    background-color: {CARD_2};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    font-family: "{FONT_FAMILY}";
    font-size: 12px;
}}
QPushButton:hover {{
    background-color: #38272d;
}}
QPushButton#primary {{
    background-color: {ACCENT};
    border: 1px solid {ACCENT_LIGHT};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#primary:hover {{
    background-color: {ACCENT_LIGHT};
}}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid transparent;
    color: {MUTED};
    padding: 6px 10px;
}}
QPushButton#ghost:hover {{
    color: {TEXT};
    background: {CARD_2};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #4a333a;
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: #63434b;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""
