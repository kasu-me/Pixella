"""Qt stylesheets for light and dark themes."""
from __future__ import annotations

LIGHT = """
QMainWindow, QWidget {
    background-color: #f5f5f5;
    color: #1a1a1a;
    font-family: "Yu Gothic UI", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QListWidget {
    background-color: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 6px;
}

QListWidget::item:selected {
    background-color: #cce0ff;
    border-radius: 4px;
}

QLineEdit {
    background-color: #ffffff;
    border: 1px solid #b0b0b0;
    border-radius: 5px;
    padding: 4px 8px;
}

QLineEdit:focus {
    border: 1.5px solid #3c82f6;
}

QPushButton {
    background-color: #e8e8e8;
    border: 1px solid #bdbdbd;
    border-radius: 5px;
    padding: 4px 12px;
}

QPushButton:hover {
    background-color: #d4d4d4;
}

QPushButton#primaryBtn {
    background-color: #3c82f6;
    color: white;
    border: none;
}

QPushButton#primaryBtn:hover {
    background-color: #2563eb;
}

QPushButton#untaggedBtn {
    background-color: #f0f2f5;
    border: 1px solid #b8bcc8;
    border-radius: 5px;
    padding: 4px 10px;
    color: #555555;
    font-size: 9pt;
}

QPushButton#untaggedBtn:hover {
    background-color: #dce3ee;
    border-color: #8899bb;
}

QPushButton#untaggedBtn:checked {
    background-color: #f59e0b;
    border-color: #d97706;
    color: #ffffff;
}

QLabel#sectionTitle {
    font-weight: bold;
    color: #555555;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

QLabel#detailPreview {
    background-color: #e0e0e0;
    border-radius: 6px;
}

QLabel#tagChip {
    background-color: #dbeafe;
    color: #1d4ed8;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 9pt;
}

QLabel#tagChipClose {
    color: #6b7280;
    font-size: 8pt;
    padding: 0 2px;
}

QLabel#tagChipClose:hover {
    color: #ef4444;
}

QFrame#divider {
    color: #d0d0d0;
}

/* ---- Breadcrumb ---- */
QWidget#breadcrumbBar {
    background-color: #edf0f5;
    border: 1px solid #d0d4dc;
    border-radius: 5px;
}

QLabel#breadcrumbLink {
    color: #3c82f6;
    font-size: 9pt;
}

QLabel#breadcrumbSep {
    color: #888888;
    font-size: 10pt;
}

QLabel#breadcrumbCurrent {
    color: #333333;
    font-size: 9pt;
    font-weight: bold;
}

/* ---- Sort Bar ---- */
QWidget#sortBar {
    background-color: #f0f2f5;
    border: 1px solid #d0d4dc;
    border-radius: 5px;
}

QLabel#sortBarLabel {
    color: #555555;
    font-size: 9pt;
}

QComboBox#sortBarCombo {
    background-color: #ffffff;
    border: 1px solid #b8bcc8;
    border-radius: 4px;
    padding: 2px 6px;
    min-width: 80px;
    color: #1a1a1a;
    font-size: 9pt;
}

QComboBox#sortBarCombo::drop-down {
    border: none;
    width: 16px;
}

QComboBox#sortBarCombo QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #b8bcc8;
    selection-background-color: #dce3ee;
}

QPushButton#sortBarDir {
    background-color: #ffffff;
    border: 1px solid #b8bcc8;
    border-radius: 4px;
    padding: 2px 6px;
    color: #1a1a1a;
    font-size: 9pt;
}

QPushButton#sortBarDir:hover {
    background-color: #dce3ee;
    border-color: #8899bb;
}

QPushButton#sortBarDir:checked {
    background-color: #3c82f6;
    border-color: #2563eb;
    color: #ffffff;
}

/* ---- ToolBar ---- */
QToolBar {
    background: #e0e4ea;
    border-bottom: 1px solid #c0c4cc;
    spacing: 4px;
    padding: 3px 4px;
}

QToolBar QToolButton {
    background-color: #f0f2f5;
    border: 1px solid #b8bcc8;
    border-radius: 5px;
    padding: 4px 10px;
    min-width: 32px;
    color: #1a1a1a;
}

QToolBar QToolButton:hover {
    background-color: #dce3ee;
    border-color: #8899bb;
}

QToolBar QToolButton:pressed {
    background-color: #c8d3e8;
    border-color: #5577bb;
}

QToolBar QToolButton:disabled {
    color: #aaaaaa;
    background-color: #eaeaea;
    border-color: #d0d0d0;
}

QToolBar QToolButton:checked {
    background-color: #3c82f6;
    color: white;
    border-color: #2563eb;
}

/* ---- MenuBar ---- */
QMenuBar {
    background-color: #e0e4ea;
    border-bottom: 1px solid #c0c4cc;
}

QMenuBar::item {
    padding: 4px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #c8d3e8;
    color: #1a1a1a;
}

/* ---- Dropdown menu ---- */
QMenu {
    background-color: #ffffff;
    border: 1px solid #c0c4cc;
    border-radius: 6px;
    padding: 4px 0;
}

QMenu::item {
    padding: 5px 28px 5px 16px;
    border-radius: 4px;
    margin: 1px 4px;
}

QMenu::item:selected {
    background-color: #dce3ee;
    color: #1a1a1a;
}

QMenu::item:disabled {
    color: #aaaaaa;
}

QMenu::separator {
    height: 1px;
    background: #d0d0d0;
    margin: 4px 8px;
}

QStatusBar {
    background: #e0e4ea;
    color: #555555;
}

QScrollArea {
    border: none;
}

/* ---- Tag Manager ---- */
QWidget#tagManagerRow {
    border-bottom: 1px solid #e0e0e0;
    border-radius: 4px;
}

QWidget#tagManagerRow:hover {
    background-color: #eef2fa;
}

QLabel#tagManagerLink {
    color: #3c82f6;
    font-size: 10pt;
}

QLabel#tagManagerCount {
    color: #888888;
    font-size: 9pt;
    min-width: 50px;
    qproperty-alignment: AlignRight;
}

QPushButton#tagManagerDelete {
    background-color: transparent;
    border: 1px solid #e0e0e0;
    border-radius: 4px;
    color: #999999;
    font-size: 9pt;
    padding: 0;
}

QPushButton#tagManagerDelete:hover {
    background-color: #fee2e2;
    border-color: #ef4444;
    color: #ef4444;
}

QPushButton#tagManagerSortBtn {
    background-color: #e8ecf4;
    border: 1px solid #d0d4dc;
    border-radius: 3px;
    color: #555555;
    font-size: 9pt;
    padding: 2px 4px;
    text-align: left;
}

QPushButton#tagManagerSortBtn:hover {
    background-color: #d8dff0;
    border-color: #b0b8cc;
    color: #3c82f6;
}

QPushButton#tagManagerSortBtn[active="true"] {
    background-color: #dce6fb;
    border-color: #3c82f6;
    color: #3c82f6;
    font-weight: bold;
}
"""

DARK = """
QMainWindow, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Yu Gothic UI", "Segoe UI", sans-serif;
    font-size: 10pt;
}

QListWidget {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
}

QListWidget::item:selected {
    background-color: #45475a;
    border-radius: 4px;
}

QLineEdit {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 4px 8px;
    color: #cdd6f4;
}

QLineEdit:focus {
    border: 1.5px solid #89b4fa;
}

QPushButton {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 4px 12px;
    color: #cdd6f4;
}

QPushButton:hover {
    background-color: #45475a;
}

QPushButton#primaryBtn {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
}

QPushButton#primaryBtn:hover {
    background-color: #74c7ec;
}
QPushButton#untaggedBtn {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 4px 10px;
    color: #cdd6f4;
    font-size: 9pt;
}

QPushButton#untaggedBtn:hover {
    background-color: #45475a;
    border-color: #6c7086;
}

QPushButton#untaggedBtn:checked {
    background-color: #f59e0b;
    border-color: #d97706;
    color: #1e1e2e;
}
QLabel#sectionTitle {
    font-weight: bold;
    color: #a6adc8;
    font-size: 8pt;
    letter-spacing: 0.5px;
}

QLabel#detailPreview {
    background-color: #313244;
    border-radius: 6px;
}

QLabel#tagChip {
    background-color: #1e3a5f;
    color: #89b4fa;
    border-radius: 10px;
    padding: 2px 8px;
    font-size: 9pt;
}

QLabel#tagChipClose {
    color: #6c7086;
    font-size: 8pt;
    padding: 0 2px;
}

QLabel#tagChipClose:hover {
    color: #f38ba8;
}

QFrame#divider {
    color: #45475a;
}

/* ---- Breadcrumb ---- */
QWidget#breadcrumbBar {
    background-color: #252535;
    border: 1px solid #3a3a50;
    border-radius: 5px;
}

QLabel#breadcrumbLink {
    color: #89b4fa;
    font-size: 9pt;
}

QLabel#breadcrumbSep {
    color: #6c7086;
    font-size: 10pt;
}

QLabel#breadcrumbCurrent {
    color: #cdd6f4;
    font-size: 9pt;
    font-weight: bold;
}

/* ---- Sort Bar ---- */
QWidget#sortBar {
    background-color: #252535;
    border: 1px solid #3a3a50;
    border-radius: 5px;
}

QLabel#sortBarLabel {
    color: #a6adc8;
    font-size: 9pt;
}

QComboBox#sortBarCombo {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 2px 6px;
    min-width: 80px;
    color: #cdd6f4;
    font-size: 9pt;
}

QComboBox#sortBarCombo::drop-down {
    border: none;
    width: 16px;
}

QComboBox#sortBarCombo QAbstractItemView {
    background-color: #313244;
    border: 1px solid #45475a;
    selection-background-color: #45475a;
    color: #cdd6f4;
}

QPushButton#sortBarDir {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 2px 6px;
    color: #cdd6f4;
    font-size: 9pt;
}

QPushButton#sortBarDir:hover {
    background-color: #363650;
    border-color: #7788bb;
}

QPushButton#sortBarDir:checked {
    background-color: #89b4fa;
    border-color: #74c7ec;
    color: #1e1e2e;
}

/* ---- ToolBar ---- */
QToolBar {
    background: #181825;
    border-bottom: 1px solid #313244;
    spacing: 4px;
    padding: 3px 4px;
}

QToolBar QToolButton {
    background-color: #2a2a3e;
    border: 1px solid #45475a;
    border-radius: 5px;
    padding: 4px 10px;
    min-width: 32px;
    color: #cdd6f4;
}

QToolBar QToolButton:hover {
    background-color: #363650;
    border-color: #7788bb;
}

QToolBar QToolButton:pressed {
    background-color: #45475a;
    border-color: #89b4fa;
}

QToolBar QToolButton:disabled {
    color: #585b70;
    background-color: #1e1e2e;
    border-color: #313244;
}

QToolBar QToolButton:checked {
    background-color: #89b4fa;
    color: #1e1e2e;
    border-color: #74c7ec;
}

/* ---- MenuBar ---- */
QMenuBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    color: #cdd6f4;
}

QMenuBar::item {
    padding: 4px 10px;
    border-radius: 4px;
}

QMenuBar::item:selected {
    background-color: #313244;
    color: #cdd6f4;
}

/* ---- Dropdown menu ---- */
QMenu {
    background-color: #24273a;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 4px 0;
    color: #cdd6f4;
}

QMenu::item {
    padding: 5px 28px 5px 16px;
    border-radius: 4px;
    margin: 1px 4px;
}

QMenu::item:selected {
    background-color: #363650;
    color: #cdd6f4;
}

QMenu::item:disabled {
    color: #585b70;
}

QMenu::separator {
    height: 1px;
    background: #45475a;
    margin: 4px 8px;
}

QStatusBar {
    background: #181825;
    color: #a6adc8;
}

QScrollArea {
    border: none;
}

/* ---- Tag Manager ---- */
QWidget#tagManagerRow {
    border-bottom: 1px solid #313244;
    border-radius: 4px;
}

QWidget#tagManagerRow:hover {
    background-color: #2a2a3e;
}

QLabel#tagManagerLink {
    color: #89b4fa;
    font-size: 10pt;
}

QLabel#tagManagerCount {
    color: #6c7086;
    font-size: 9pt;
    min-width: 50px;
    qproperty-alignment: AlignRight;
}

QPushButton#tagManagerDelete {
    background-color: transparent;
    border: 1px solid #45475a;
    border-radius: 4px;
    color: #6c7086;
    font-size: 9pt;
    padding: 0;
}

QPushButton#tagManagerDelete:hover {
    background-color: #3b1a1a;
    border-color: #f38ba8;
    color: #f38ba8;
}

QPushButton#tagManagerSortBtn {
    background-color: #2a2a3e;
    border: 1px solid #45475a;
    border-radius: 3px;
    color: #a6adc8;
    font-size: 9pt;
    padding: 2px 4px;
    text-align: left;
}

QPushButton#tagManagerSortBtn:hover {
    background-color: #313244;
    border-color: #6c7086;
    color: #89b4fa;
}

QPushButton#tagManagerSortBtn[active="true"] {
    background-color: #1e3a5f;
    border-color: #89b4fa;
    color: #89b4fa;
    font-weight: bold;
}
"""


def apply_theme(app, dark: bool) -> None:
    app.setStyleSheet(DARK if dark else LIGHT)
