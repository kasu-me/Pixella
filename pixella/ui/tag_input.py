"""Tag input widget with autocomplete."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QCompleter, QHBoxLayout, QLabel, QLineEdit,
    QSizePolicy, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QKeyEvent


class _ChipContainer(QWidget):
    """
    チップを折り返し表示するコンテナ。
    QLayout サブクラスを使わず、resizeEvent で手動配置 + setFixedHeight で高さを設定する。
    """
    _H_GAP = 4
    _V_GAP = 4
    _MARGIN = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._chips: list[QWidget] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self._MARGIN * 2)

    def set_chips(self, chips: list[QWidget]) -> None:
        for old in self._chips:
            old.deleteLater()
        self._chips = chips
        for chip in chips:
            chip.setParent(self)
            chip.show()
        self._relayout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w = self.width()
        if w <= 0 or not self._chips:
            self.setFixedHeight(self._MARGIN * 2)
            return

        x, y, row_h = self._MARGIN, self._MARGIN, 0
        for chip in self._chips:
            sz = chip.sizeHint()
            if x > self._MARGIN and x + sz.width() > w - self._MARGIN:
                x = self._MARGIN
                y += row_h + self._V_GAP
                row_h = 0
            chip.setGeometry(x, y, sz.width(), sz.height())
            x += sz.width() + self._H_GAP
            row_h = max(row_h, sz.height())

        new_h = y + row_h + self._MARGIN
        self.setFixedHeight(new_h)


class TagChip(QWidget):
    removed = Signal(str)

    def __init__(self, tag: str, color: str | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tag = tag
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 4, 2)
        layout.setSpacing(2)

        self._label = QLabel(tag)
        self._label.setObjectName("tagChip")

        self._btn = QLabel("✕")
        self._btn.setObjectName("tagChipClose")
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.mousePressEvent = lambda _e: self.removed.emit(self._tag)

        layout.addWidget(self._label)
        layout.addWidget(self._btn)

        if color:
            self._apply_color(color)

    def _apply_color(self, color: str) -> None:
        """#rrggbb の背景色から文字色を自動調整してインラインスタイルを適用。"""
        from PySide6.QtGui import QColor
        bg = QColor(color)
        # 輝度に応じてテキスト色を決定
        luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
        fg = "#ffffff" if luminance < 140 else "#111111"
        self._label.setStyleSheet(
            f"background-color: {color}; color: {fg};"
            "border-radius: 10px; padding: 2px 8px; font-size: 9pt;"
        )
        self._btn.setStyleSheet(
            "color: #333333; font-size: 8pt; padding: 0 2px;"
        )

    @property
    def tag(self) -> str:
        return self._tag


class TagInputWidget(QWidget):
    """
    Shows current tags as chips and provides an autocomplete input
    for adding new tags.
    Emits `tags_changed` with the current list when it changes.
    """

    tags_changed = Signal(list)  # list[str] — 全タグリスト
    tag_added    = Signal(str)   # 追加された1タグ名
    tag_removed  = Signal(str)   # 削除された1タグ名

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tags: list[str] = []
        self._all_tags: list[str] = []
        self._color_map: dict[str, str | None] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Chip container (manual flow layout via resizeEvent)
        self._chip_container = _ChipContainer()

        # Input
        self._input = QLineEdit()
        self._input.setPlaceholderText("タグを入力して Enter…")
        self._input.returnPressed.connect(self._on_return)

        self._completer = QCompleter(self._all_tags)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._input.setCompleter(self._completer)
        self._completer.activated.connect(self._on_completed)

        outer.addWidget(self._chip_container)
        outer.addWidget(self._input)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_completion_list(self, tags: list[str]) -> None:
        self._all_tags = tags
        self._completer.model().setStringList(tags)  # type: ignore[union-attr]

    def set_color_map(self, color_map: dict[str, str | None]) -> None:
        """タグ名 → カラーの対応表を設定し、チップを再描画する。"""
        self._color_map = color_map
        self._rebuild_chips()

    def set_tags(self, tags: list[str]) -> None:
        self._tags = list(tags)
        self._rebuild_chips()

    def get_tags(self) -> list[str]:
        return list(self._tags)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_return(self) -> None:
        popup = self._completer.popup()
        if popup.isVisible():
            # 矢印キーでいずれかの候補が選択されている場合は
            # activated シグナル (_on_completed) に処理を任せる
            if popup.currentIndex().row() >= 0:
                return
            # 候補が選択されていない（矢印未操作）場合は
            # ポップアップを閉じて入力テキストをそのまま追加する
            popup.hide()
        text = self._input.text().strip().lower()
        if text and text not in self._tags:
            self._tags.append(text)
            self._rebuild_chips()
            self.tags_changed.emit(self._tags)
            self.tag_added.emit(text)
        self._input.clear()

    def _on_completed(self, text: str) -> None:
        """コンプリーターのポップアップから選択されたときに呼ばれる。"""
        from PySide6.QtCore import QTimer
        text = text.strip().lower()
        if text and text not in self._tags:
            self._tags.append(text)
            self._rebuild_chips()
            self.tags_changed.emit(self._tags)
            self.tag_added.emit(text)
        # コンプリーターがテキストを再セットした後でクリアするため singleShot で遅延
        QTimer.singleShot(0, self._input.clear)

    def _rebuild_chips(self) -> None:
        chips = []
        for tag in self._tags:
            chip = TagChip(tag, color=self._color_map.get(tag))
            chip.removed.connect(self._remove_tag)
            chips.append(chip)
        self._chip_container.set_chips(chips)

    def _remove_tag(self, tag: str) -> None:
        if tag in self._tags:
            self._tags.remove(tag)
            self._rebuild_chips()
            self.tags_changed.emit(self._tags)
            self.tag_removed.emit(tag)
