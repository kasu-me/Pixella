"""Search bar widget with tag-based autocomplete."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget,
)


class SearchBar(QWidget):
    search_requested   = Signal(list)   # list[str] — tag names
    cleared            = Signal()
    untagged_requested = Signal()       # タグなし絞り込み

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._input = QLineEdit()
        self._input.setPlaceholderText("タグで検索 (スペース区切りで複数タグ AND 検索)…")
        self._input.returnPressed.connect(self._emit_search)
        self._input.textChanged.connect(self._on_text_changed)

        self._completer = QCompleter([])
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._input.setCompleter(self._completer)

        self._search_btn = QPushButton("検索")
        self._search_btn.setObjectName("primaryBtn")
        self._search_btn.clicked.connect(self._emit_search)

        self._untagged_btn = QPushButton("タグなし")
        self._untagged_btn.setObjectName("untaggedBtn")
        self._untagged_btn.setCheckable(True)
        self._untagged_btn.toggled.connect(self._on_untagged_toggled)

        self._clear_btn = QPushButton("✕")
        self._clear_btn.setFixedWidth(32)
        self._clear_btn.clicked.connect(self._clear)

        layout.addWidget(self._input, 1)
        layout.addWidget(self._untagged_btn)
        layout.addWidget(self._search_btn)
        layout.addWidget(self._clear_btn)

    def set_completion_list(self, tags: list[str]) -> None:
        self._completer.model().setStringList(tags)  # type: ignore[union-attr]

    def set_text(self, text: str) -> None:
        """外部からテキストをセットする（検索は実行しない）。"""
        self._untagged_btn.blockSignals(True)
        self._untagged_btn.setChecked(False)
        self._untagged_btn.blockSignals(False)
        self._input.setText(text)

    def _emit_search(self) -> None:
        text = self._input.text().strip()
        tags = [t.strip().lower() for t in text.split() if t.strip()]
        if tags:
            # テキスト検索するとき「タグなし」ボタンを解除
            self._untagged_btn.blockSignals(True)
            self._untagged_btn.setChecked(False)
            self._untagged_btn.blockSignals(False)
            self.search_requested.emit(tags)
        else:
            self.cleared.emit()

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self.cleared.emit()

    def _on_untagged_toggled(self, checked: bool) -> None:
        if checked:
            # 入力欄をサイレントにクリア (textChanged → cleared が出ないよう blockSignals)
            self._input.blockSignals(True)
            self._input.clear()
            self._input.blockSignals(False)
            self.untagged_requested.emit()
        else:
            self.cleared.emit()

    def _clear(self) -> None:
        self._untagged_btn.blockSignals(True)
        self._untagged_btn.setChecked(False)
        self._untagged_btn.blockSignals(False)
        self._input.clear()
        self.cleared.emit()
