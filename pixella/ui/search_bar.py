"""Search bar widget with chip-based tag selection (Web Viewer style)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)


# ── Utilities ───────────────────────────────────────────────────────────────

def _contrast_color(hex_color: str) -> str:
    """Return '#000000' or '#ffffff' for best readability on the given background."""
    c = hex_color.lstrip('#')
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return '#000000' if luminance > 128 else '#ffffff'


# ── Filter input (IME-aware) ──────────────────────────────────────────────

class _FilterInput(QLineEdit):
    """タグ絞り込み用 QLineEdit。
    IME 入力途中の Enter（変換確定）では発火せず、入力確定後の Enter のみ confirmed を発火する。
    """

    confirmed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._composing = False

    def inputMethodEvent(self, event) -> None:  # type: ignore[override]
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if not self._composing:
                self.confirmed.emit()
                return
        super().keyPressEvent(event)


# ── Flow chip container ───────────────────────────────────────────────────────

class _ChipContainer(QWidget):
    """
    Flow-layout container for chip widgets.
    Wraps chips like CSS ``flex-wrap`` and adjusts its own height automatically
    so that a parent QScrollArea can show/hide a scrollbar as needed.
    """

    _H_GAP = 6
    _V_GAP = 5
    _MARGIN = 4

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

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        w = self.width()
        if w <= 0:
            return
        if not self._chips:
            self.setFixedHeight(self._MARGIN * 2)
            return
        x = y = self._MARGIN
        row_h = 0
        for chip in self._chips:
            sz = chip.sizeHint()
            if x > self._MARGIN and x + sz.width() > w - self._MARGIN:
                x = self._MARGIN
                y += row_h + self._V_GAP
                row_h = 0
            chip.setGeometry(x, y, sz.width(), sz.height())
            x += sz.width() + self._H_GAP
            row_h = max(row_h, sz.height())
        self.setFixedHeight(y + row_h + self._MARGIN)


# ── Chip widgets ──────────────────────────────────────────────────────────────

class _SelectedChip(QPushButton):
    """Selected tag chip — click anywhere to remove."""

    removed = Signal(str)

    def __init__(self, tag: str, parent: QWidget | None = None) -> None:
        super().__init__(f"{tag}  ✕", parent)
        self._tag = tag
        self.setObjectName("searchSelectedChip")
        self.clicked.connect(lambda: self.removed.emit(self._tag))

    @property
    def tag(self) -> str:
        return self._tag


class _AvailChip(QPushButton):
    """Available tag chip — click to add to the selection."""

    chosen = Signal(str)

    def __init__(
        self,
        tag: str,
        count: int = 0,
        color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        label = f"{tag} ({count})" if count > 0 else tag
        super().__init__(label, parent)
        self._tag = tag
        self.setObjectName("searchAvailChip")
        if color:
            text_color = _contrast_color(color)
            self.setStyleSheet(
                f"QPushButton#searchAvailChip {{"
                f" background-color: {color}; color: {text_color};"
                f" border-color: {color}; }}"
                f" QPushButton#searchAvailChip:hover {{"
                f" background-color: {color}; opacity: 0.85; }}"
            )
        self.clicked.connect(lambda: self.chosen.emit(self._tag))

    @property
    def tag(self) -> str:
        return self._tag


# ── SearchBar ─────────────────────────────────────────────────────────────────

class SearchBar(QWidget):
    """
    Web-viewer-style tag search bar.

    - Selected tags are displayed as removable chips.
    - Available tags are listed as clickable chips (with a filter input).
    - AND / OR mode toggle mirrors the web viewer's mode-toggle buttons.

    Public API (unchanged from the previous text-input version):
        set_completion_list(tags)   — populate available tag chips
        set_text(text)              — silently set a selected tag (no signal)
    Signals:
        search_requested(list[str], str)  — tags + mode
        cleared()
        untagged_requested()
    """

    search_requested   = Signal(list, str)   # list[str] tags, mode "and"|"or"|"exact"
    cleared            = Signal()
    untagged_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("searchBarPanel")
        self._selected: list[str] = []
        self._all_tags: list[tuple[str, int, str | None]] = []
        self._mode: str = "and"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(8)

        # ── Row 1: selected chips + mode toggle + untagged + clear ────────
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(8)

        # Left: placeholder label or selected chip container
        self._chips_area = QWidget()
        self._chips_area.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        chips_vbox = QVBoxLayout(self._chips_area)
        chips_vbox.setContentsMargins(0, 0, 0, 0)
        chips_vbox.setSpacing(0)

        self._placeholder = QLabel("タグを選択して絞り込み…")
        self._placeholder.setObjectName("searchPlaceholder")
        chips_vbox.addWidget(self._placeholder)

        self._sel_chips = _ChipContainer()
        self._sel_chips.hide()
        chips_vbox.addWidget(self._sel_chips)

        row1.addWidget(self._chips_area, 1)

        # AND / OR mode toggle
        mode_frame = QFrame()
        mode_frame.setObjectName("modeToggleFrame")
        mode_layout = QHBoxLayout(mode_frame)
        mode_layout.setContentsMargins(1, 1, 1, 1)
        mode_layout.setSpacing(0)

        self._btn_and = QPushButton("AND")
        self._btn_and.setObjectName("modeAndBtn")
        self._btn_and.setCheckable(True)
        self._btn_and.setChecked(True)
        self._btn_and.setFixedWidth(46)
        self._btn_and.clicked.connect(self._on_and_clicked)

        self._btn_or = QPushButton("OR")
        self._btn_or.setObjectName("modeOrBtn")
        self._btn_or.setCheckable(True)
        self._btn_or.setFixedWidth(40)
        self._btn_or.clicked.connect(self._on_or_clicked)

        self._btn_exact = QPushButton("完全一致")
        self._btn_exact.setObjectName("modeExactBtn")
        self._btn_exact.setCheckable(True)
        self._btn_exact.clicked.connect(self._on_exact_clicked)

        mode_layout.addWidget(self._btn_and)
        mode_layout.addWidget(self._btn_or)
        mode_layout.addWidget(self._btn_exact)
        row1.addWidget(mode_frame)

        # タグなし toggle
        self._untagged_btn = QPushButton("タグなし")
        self._untagged_btn.setObjectName("untaggedBtn")
        self._untagged_btn.setCheckable(True)
        self._untagged_btn.toggled.connect(self._on_untagged_toggled)
        row1.addWidget(self._untagged_btn)

        # Clear button
        self._clear_btn = QPushButton("クリア")
        self._clear_btn.setObjectName("clearBtn")
        self._clear_btn.clicked.connect(self._clear)
        row1.addWidget(self._clear_btn)

        outer.addLayout(row1)

        # ── Row 2: tag filter input ───────────────────────────────────────
        self._filter_input = _FilterInput()
        self._filter_input.setObjectName("tagFilterInput")
        self._filter_input.setPlaceholderText("タグを絞り込み…")
        self._filter_input.textChanged.connect(self._on_filter_changed)
        self._filter_input.confirmed.connect(self._on_filter_confirmed)
        outer.addWidget(self._filter_input)

        # ── Row 3: available tags ─────────────────────────────────────────
        avail_section = QWidget()
        avail_vbox = QVBoxLayout(avail_section)
        avail_vbox.setContentsMargins(0, 0, 0, 0)
        avail_vbox.setSpacing(4)

        self._avail_header = QLabel("利用可能なタグ")
        self._avail_header.setObjectName("availTagsHeader")
        avail_vbox.addWidget(self._avail_header)

        self._avail_scroll = QScrollArea()
        self._avail_scroll.setObjectName("availTagsScroll")
        self._avail_scroll.setWidgetResizable(True)
        self._avail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._avail_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._avail_scroll.setFixedHeight(120)

        self._avail_chips = _ChipContainer()
        self._avail_scroll.setWidget(self._avail_chips)
        avail_vbox.addWidget(self._avail_scroll)

        outer.addWidget(avail_section)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_completion_list(self, tags: list[tuple[str, int, str | None]]) -> None:
        """利用可能なタグリストをセットする。(タグ名, 件数, カラー) のリスト。"""
        self._all_tags = list(tags)
        self._rebuild_available()

    def set_text(self, text: str) -> None:
        """
        外部からタグをセット（タグマネージャ等からの呼び出し）。
        検索シグナルは発行しない。呼び出し側が必要に応じて処理すること。
        空文字を渡すと選択状態をサイレントにクリアする。
        """
        self._untagged_btn.blockSignals(True)
        self._untagged_btn.setChecked(False)
        self._untagged_btn.blockSignals(False)
        tag = text.strip().lower()
        if tag:
            if tag not in self._selected:
                self._selected.append(tag)
                self._rebuild_selected()
                self._rebuild_available()
        else:
            self._selected.clear()
            self._filter_input.blockSignals(True)
            self._filter_input.clear()
            self._filter_input.blockSignals(False)
            self._rebuild_selected()
            self._rebuild_available()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _add_tag(self, tag: str) -> None:
        if tag in self._selected:
            return
        self._untagged_btn.blockSignals(True)
        self._untagged_btn.setChecked(False)
        self._untagged_btn.blockSignals(False)
        self._selected.append(tag)
        self._filter_input.blockSignals(True)
        self._filter_input.clear()
        self._filter_input.blockSignals(False)
        self._rebuild_selected()
        self._rebuild_available()
        self._emit_search()

    def _remove_tag(self, tag: str) -> None:
        if tag in self._selected:
            self._selected.remove(tag)
        self._rebuild_selected()
        self._rebuild_available()
        if self._selected:
            self._emit_search()
        else:
            self.cleared.emit()

    def _rebuild_selected(self) -> None:
        if self._selected:
            chips: list[QWidget] = []
            for tag in self._selected:
                chip = _SelectedChip(tag)
                chip.removed.connect(self._remove_tag)
                chips.append(chip)
            self._sel_chips.set_chips(chips)
            self._sel_chips.show()
            self._placeholder.hide()
        else:
            self._sel_chips.set_chips([])
            self._sel_chips.hide()
            self._placeholder.show()

    def _rebuild_available(self) -> None:
        filt = self._filter_input.text().strip().lower()
        chips: list[QWidget] = []
        for tag, count, color in self._all_tags:
            if tag in self._selected:
                continue
            if filt and filt not in tag.lower():
                continue
            chip = _AvailChip(tag, count, color)
            chip.chosen.connect(self._add_tag)
            chips.append(chip)
        self._avail_chips.set_chips(chips)

    def _on_filter_changed(self, _text: str) -> None:
        self._rebuild_available()

    def _on_filter_confirmed(self) -> None:
        """タグ絞り込み欄でEnter確定時、入力内容と完全一致するタグを追加する。"""
        text = self._filter_input.text().strip().lower()
        if not text:
            return
        for tag, _count, _color in self._all_tags:
            if tag == text and tag not in self._selected:
                self._add_tag(tag)  # 内部でフィルターもクリアされる
                return

    def _on_and_clicked(self) -> None:
        self._mode = "and"
        self._btn_and.setChecked(True)
        self._btn_or.setChecked(False)
        self._btn_exact.setChecked(False)
        if self._selected:
            self._emit_search()

    def _on_or_clicked(self) -> None:
        self._mode = "or"
        self._btn_and.setChecked(False)
        self._btn_or.setChecked(True)
        self._btn_exact.setChecked(False)
        if self._selected:
            self._emit_search()

    def _on_exact_clicked(self) -> None:
        self._mode = "exact"
        self._btn_and.setChecked(False)
        self._btn_or.setChecked(False)
        self._btn_exact.setChecked(True)
        if self._selected:
            self._emit_search()

    def _emit_search(self) -> None:
        if self._selected:
            self.search_requested.emit(list(self._selected), self._mode)
        else:
            self.cleared.emit()

    def _on_untagged_toggled(self, checked: bool) -> None:
        if checked:
            self._selected.clear()
            self._rebuild_selected()
            self._rebuild_available()
            self.untagged_requested.emit()
        else:
            self.cleared.emit()

    def _clear(self) -> None:
        self._untagged_btn.blockSignals(True)
        self._untagged_btn.setChecked(False)
        self._untagged_btn.blockSignals(False)
        self._selected.clear()
        self._filter_input.clear()
        self._rebuild_selected()
        self._rebuild_available()
        self.cleared.emit()
