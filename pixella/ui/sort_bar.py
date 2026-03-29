"""Sort bar widget — sort key selector and ascending/descending toggle."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget


class SortBar(QWidget):
    """並び順コントロール。sort_changed(key, is_descending) を発火する。"""

    sort_changed = Signal(str, bool)   # ('added'|'created'|'name', is_desc)

    _KEYS = [
        ("added",   "追加順"),
        ("created", "作成日順"),
        ("name",    "名前順"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("sortBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        lbl = QLabel("並び順:")
        lbl.setObjectName("sortBarLabel")
        layout.addWidget(lbl)

        self._combo = QComboBox()
        self._combo.setObjectName("sortBarCombo")
        for key, label in self._KEYS:
            self._combo.addItem(label, key)
        self._combo.currentIndexChanged.connect(self._emit)
        layout.addWidget(self._combo)

        self._dir_btn = QPushButton("↑ 昇順")
        self._dir_btn.setObjectName("sortBarDir")
        self._dir_btn.setCheckable(True)
        self._dir_btn.setChecked(False)
        self._dir_btn.setFixedWidth(72)
        self._dir_btn.toggled.connect(self._on_toggled)
        layout.addWidget(self._dir_btn)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def current_key(self) -> str:
        return self._combo.currentData()

    @property
    def is_descending(self) -> bool:
        return self._dir_btn.isChecked()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_toggled(self, checked: bool) -> None:
        self._dir_btn.setText("↓ 降順" if checked else "↑ 昇順")
        self._emit()

    def _emit(self) -> None:
        self.sort_changed.emit(self._combo.currentData(), self._dir_btn.isChecked())
