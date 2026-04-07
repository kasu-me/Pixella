"""Tag input widget with autocomplete."""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QSizePolicy, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QFocusEvent, QInputMethodEvent, QKeyEvent


_MAX_SUGGESTIONS = 50  # 表示する候補数の上限


class _CompletionPopup(QListWidget):
    """
    キーボードフォーカスを奪わない補完ポップアップ。

    標準の QCompleter は内部ポップアップを Qt::Popup ウィンドウ（キーボードグラブ）
    として表示するため、QLineEdit へのキーイベントが届かなくなる。
    これを根本解決するために、以下を組み合わせたカスタムポップアップを使う:
      - Qt::Tool | Qt::FramelessWindowHint  : フレームなしの軽量ウィンドウ
      - WA_ShowWithoutActivating            : 表示してもアクティブウィンドウを変えない
      - FocusPolicy.NoFocus                 : クリックしてもフォーカスが移動しない
    これにより QLineEdit が常にフォーカスを保持し、キー入力・IME が正常に機能する。
    """

    item_chosen = Signal(str)

    def __init__(self, anchor: "QLineEdit") -> None:
        # anchor のトップレベルウィンドウを親にする
        # → メインウィンドウと一緒に最小化・アクティブ切り替えが連動する
        parent_win = anchor.window()
        super().__init__(parent_win if parent_win is not None else anchor)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus  # WS_EX_NOACTIVATE: 表示時にメインウィンドウを非アクティブにしない
        )
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("completionPopup")
        self._anchor = anchor
        self.itemClicked.connect(lambda item: self.item_chosen.emit(item.text()))

    def set_items(self, items: list[str]) -> None:
        self.clear()
        for text in items:
            self.addItem(text)
        self.setCurrentRow(-1)  # 初期状態は未選択

    def navigate(self, delta: int) -> None:
        """
        delta=+1 で下へ、-1 で上へ移動する。
        先頭より上に行くと選択解除（入力テキストに戻る）。
        末尾より下には行かない。
        """
        n = self.count()
        if n == 0:
            return
        cur = self.currentRow()
        if cur < 0:
            self.setCurrentRow(0 if delta > 0 else n - 1)
        else:
            new_row = cur + delta
            if new_row < 0:
                self.setCurrentRow(-1)  # 選択解除
            elif new_row >= n:
                self.setCurrentRow(n - 1)
            else:
                self.setCurrentRow(new_row)

    def current_text(self) -> str | None:
        item = self.currentItem()
        return item.text() if item else None

    def reposition(self) -> None:
        """アンカーウィジェット（QLineEdit）の直下に位置を合わせる。"""
        anchor = self._anchor
        pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        row_h = self.sizeHintForRow(0) if self.count() > 0 else 24
        h = min(row_h * self.count() + 4, 200)
        self.setGeometry(pos.x(), pos.y(), anchor.width(), h)

    def show_popup(self) -> None:
        self.reposition()
        self.show()
        self.raise_()


class _TagLineEdit(QLineEdit):
    """
    カスタム補完ポップアップ付き QLineEdit。

    QCompleter を使わず自前で補完ポップアップを管理することで、
    Windows IME 環境でのフォーカス奪取・キーナビゲーション不具合を根本解決する。

    動作フロー:
    - 文字入力 (ASCII)     : keyPressEvent → super() → _update_popup()
    - IME プリエディット   : inputMethodEvent → super() → _update_popup(preedit)
    - IME 確定             : inputMethodEvent → super() (text 更新) → _update_popup("")
    - ↓/↑ キー            : popup.navigate() を直接呼び出し (super 呼ばず)
    - Enter (候補選択中)   : suggestion_confirmed を emit して確定
    - Enter (候補未選択)   : popup を閉じて returnPressed を発火させる
    - Escape               : popup を閉じる
    """

    suggestion_confirmed = Signal(str)  # ユーザーが補完候補を選択確定したとき

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_tags: list[str] = []
        self._popup: _CompletionPopup | None = None
        # 確定済みテキストが変化したときにポップアップを更新する。
        # keyPressEvent 末尾で無条件に呼ぶ方式だと、F10 および矢印キーのように
        # event.text()=='' かつ self.text()=='' (プリエディット中) のキーで
        # _update_popup('') が呼ばれてポップアップを間違えて閉じてしまう。
        # textEdited はユーザー操作でテキストが実際に変化したときのみ発火するため、
        # 不要な隅間が生じない。
        self.textEdited.connect(lambda _: self._schedule_update_popup())

    def set_all_tags(self, tags: list[str]) -> None:
        self._all_tags = tags

    def _ensure_popup(self) -> _CompletionPopup:
        if self._popup is None:
            self._popup = _CompletionPopup(self)
            self._popup.item_chosen.connect(self._on_item_clicked)
        return self._popup

    def _on_item_clicked(self, text: str) -> None:
        """ポップアップのアイテムをマウスクリックで選択したとき。"""
        self._ensure_popup().hide()
        self.clear()
        self.suggestion_confirmed.emit(text)

    def _update_popup(self, extra_preedit: str = "") -> None:
        """
        現在の入力テキスト + IME プリエディットを合わせてポップアップを更新する。
        extra_preedit は inputMethodEvent から渡される現在のプリエディット文字列。
        """
        raw = (self.text() + extra_preedit).strip()
        query = raw.lower()

        if not query:
            if self._popup:
                self._popup.hide()
            return

        matches = [t for t in self._all_tags if query in t.lower()][:_MAX_SUGGESTIONS]
        if not matches:
            if self._popup:
                self._popup.hide()
            return

        popup = self._ensure_popup()
        # 上下キーナビゲート中に inputMethodEvent などから再呼び出しがあっても
        # 現在の選択をリセットしないよう、選択済みアイテムを保持する。
        prev_selected = popup.current_text()
        popup.set_items(matches)
        if prev_selected is not None and prev_selected in matches:
            popup.setCurrentRow(matches.index(prev_selected))
        popup.show_popup()

    def _schedule_update_popup(self, preedit: str = "") -> None:
        """
        ポップアップ更新を次のイベントループ処理まで遅延させる。

        _update_popup() を keyPressEvent / inputMethodEvent の中で直接呼ぶと、
        popup.show_popup() が QWidget::show() を経由して Windows メッセージポンプを
        駆動し、フォーカス切り替えイベントが再入してくる場合がある。
        QTimer.singleShot(0) で遅延することで、呼び出し元のイベントハンドラが
        完全に返った後にポップアップ更新が実行されるよう保証する。
        """
        QTimer.singleShot(0, lambda: self._update_popup(preedit))

    def focusInEvent(self, event: QFocusEvent) -> None:
        # Qt の QLineEdit::focusInEvent は TabFocusReason / ActiveWindowFocusReason 時に
        # selectAll() を呼び出す。タグ入力欄では既存テキストを全選択する必要がない
        # (全選択中に IME commitString が来ると既存テキストが消える)。
        # すべてのフォーカスイベントを OtherFocusReason に差し替えて super() に渡すことで
        # selectAll() 自体を呼び出させない。
        neutral = QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.OtherFocusReason)
        super().focusInEvent(neutral)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        popup = self._popup
        key = event.key()

        if popup and popup.isVisible():
            if key == Qt.Key.Key_Down:
                popup.navigate(+1)
                return
            elif key == Qt.Key.Key_Up:
                popup.navigate(-1)
                return
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                chosen = popup.current_text()
                if chosen is not None:
                    # 候補が選択されている → 確定して終了
                    popup.hide()
                    self.clear()
                    self.suggestion_confirmed.emit(chosen)
                    return
                # 候補未選択 → ポップアップだけ閉じて returnPressed を発火させる
                popup.hide()
                # fall through → super() が returnPressed を発火
            elif key == Qt.Key.Key_Escape:
                popup.hide()
                return

        # 安全網: 不準な selectAll() が呼ばれていた場合の保存処理。
        # 全テキストが選択された状態で印字可能キーが押されたら、
        # テキストを置換する代わりにカーソルを末尾に移動して追記する。
        if (
            self.hasSelectedText()
            and event.text()
            and self.selectedText() == self.text()
            and not (event.modifiers() & (
                Qt.KeyboardModifier.ControlModifier
                | Qt.KeyboardModifier.AltModifier
            ))
        ):
            self.deselect()
            self.setCursorPosition(len(self.text()))

        super().keyPressEvent(event)
        # ポップアップ更新は textEdited シグナル経由で行うため、ここでは呼ばない。
        # keyPressEvent でテキストを変化させないキー (F10・矢印等) の場合に
        # _schedule_update_popup('') を呼ぶと、preedit 中 (self.text()=='') に
        # popup.hide() が実行されてポップアップが誤って消えてしまう。

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        # preedit を先にキャプチャ (super() 後は変わる可能性があるため)
        preedit = event.preeditString()
        # 安全網: 全選択状態で commitString または preedit が来た場合、
        # super() 内部 (QWidgetLineControl) が選択テキストを削除してから挿入するため
        # 既存テキストが消える。super() の前に必ず deselect して防ぐ。
        if self.hasSelectedText():
            self.deselect()
            self.setCursorPosition(len(self.text()))
        super().inputMethodEvent(event)
        # IME プリエディット中もポップアップを遅延更新する。
        # super() 呼び出し後、self.text() は確定済みテキストのみを含む。
        # preedit を lambda でキャプチャして正しい文字列を渡す。
        self._schedule_update_popup(preedit)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # フォーカスが外れたらポップアップを閉じる。
        # _CompletionPopup は NoFocus なのでポップアップクリックではここは呼ばれない。
        if self._popup:
            self._popup.hide()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        if self._popup:
            self._popup.hide()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self._popup and self._popup.isVisible():
            self._popup.reposition()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._popup and self._popup.isVisible():
            self._popup.reposition()


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
        self._color_map: dict[str, str | None] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # Chip container (manual flow layout via resizeEvent)
        self._chip_container = _ChipContainer()

        # Input (_TagLineEdit が補完ポップアップを自己管理する。QCompleter は使わない)
        self._input = _TagLineEdit()
        self._input.setPlaceholderText("タグを入力して Enter…")
        self._input.returnPressed.connect(self._on_return)
        self._input.suggestion_confirmed.connect(self._on_suggestion_confirmed)

        outer.addWidget(self._chip_container)
        outer.addWidget(self._input)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_completion_list(self, tags: list[str]) -> None:
        self._input.set_all_tags(tags)

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
        """Enter キー（補完候補未選択）でテキストをそのままタグとして追加する。"""
        text = self._input.text().strip().lower()
        if text and text not in self._tags:
            self._tags.append(text)
            self._rebuild_chips()
            self.tags_changed.emit(self._tags)
            self.tag_added.emit(text)
        self._input.clear()

    def _on_suggestion_confirmed(self, text: str) -> None:
        """補完ポップアップから候補が確定されたときに呼ばれる。"""
        text = text.strip().lower()
        if text and text not in self._tags:
            self._tags.append(text)
            self._rebuild_chips()
            self.tags_changed.emit(self._tags)
            self.tag_added.emit(text)
        # _TagLineEdit 側で clear() 済み

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
