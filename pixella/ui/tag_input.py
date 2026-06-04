"""Tag input widget with autocomplete."""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox,
    QSizePolicy, QVBoxLayout, QWidget,
)
from PySide6.QtGui import QFocusEvent, QInputMethodEvent, QKeyEvent


_MAX_SUGGESTIONS = 50  # 表示する候補数の上限


class _CompletionPopup(QListWidget):
    """
    キーボードフォーカスを奪わない補完ポップアップ。

    【設計の要点 — IME 文字落ち対策】
    このポップアップは「独立したトップレベルウィンドウ」ではなく、アンカー
    （QLineEdit）が属するトップレベルウィンドウの *子ウィジェット* として
    オーバーレイ表示する。これが IME 不具合の根本対策である。

    かつては Qt::Tool のトップレベルウィンドウとして実装していた。しかし
    Windows では、別ウィンドウの show()/hide()/setGeometry が内部で Win32 の
    ウィンドウ操作（メッセージポンプ駆動）を行い、その過程で QLineEdit の
    IME 入力コンテキストが再初期化されることがある。IME 英数モードでは
    1 文字ごとに確定テキストが増えるため毎キーでポップアップを出し入れし、
    特に「非表示→表示」へ遷移する最初のキーでこの撹乱が起き、先頭文字が
    取りこぼされることがあった（日本語モードでは変換確定の合間にしか
    ポップアップが出ないため再現しなかった）。

    子ウィジェット（ネイティブ HWND を持たない alien widget）にすると、
    show()/hide()/setGeometry はすべて Qt 内部の描画操作で完結し、ネイティブ
    ウィンドウ操作も IME コンテキストの撹乱も発生しない。表示はトップレベル
    ウィンドウ内にクリップされるが、タグ入力欄は詳細パネル上部にあり下方向に
    十分な余地があるため実用上の問題はない。
    """

    item_chosen = Signal(str)

    def __init__(self, anchor: "QLineEdit") -> None:
        # アンカーのトップレベルウィンドウを「親」にして、その内側にオーバーレイ
        # する子ウィジェットとして生成する。トップレベルウィンドウ化する
        # ウィンドウフラグ（Qt::Tool 等）は *設定しない* ことが重要。
        host = anchor.window()
        super().__init__(host if host is not None else anchor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # クリックしてもフォーカスを奪わない
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("completionPopup")
        self._anchor = anchor
        self.hide()  # 生成直後は非表示
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
        """アンカー（QLineEdit）の直下（収まらなければ直上）へ、親座標系で配置する。

        子ウィジェットはトップレベルウィンドウ内にクリップされるため、下方向に
        収まらない場合は上方向へ開いて候補が隠れないようにする。
        """
        host = self.parentWidget()
        if host is None:
            return
        anchor = self._anchor
        row_h = self.sizeHintForRow(0) if self.count() > 0 else 24
        h = min(row_h * self.count() + 4, 200)
        # アンカー下端のグローバル座標 → 親（トップレベルウィンドウ）ローカル座標へ変換。
        # 子ウィジェットなので setGeometry は親座標系で行う必要がある。
        below = host.mapFromGlobal(anchor.mapToGlobal(QPoint(0, anchor.height())))
        if below.y() + h > host.height():
            # 下方向に収まらない → アンカー上端の直上へ開く
            above = host.mapFromGlobal(anchor.mapToGlobal(QPoint(0, 0)))
            y = max(0, above.y() - h)
        else:
            y = below.y()
        self.setGeometry(below.x(), y, anchor.width(), h)

    def show_popup(self) -> None:
        self.reposition()
        self.show()
        self.raise_()


class _TagLineEdit(QLineEdit):
    """
    カスタム補完ポップアップ付き QLineEdit。

    補完候補は QCompleter を使わず、_CompletionPopup（トップレベルウィンドウ
    ではなく親ウィンドウ内の子オーバーレイ）で自前管理する。これにより
    Windows IME 環境でのフォーカス奪取・先頭文字の取りこぼしを根本的に防ぐ。

    IME 制御方針（search_bar._FilterInput と同じ単純な方式）:
    - inputMethodEvent では変換途中（プリエディット）かどうかだけを追跡する。
    - 変換途中はポップアップを隠し、確定テキストが変化したとき
      （textEdited／変換確定）のみポップアップを更新する。
    ポップアップの出し入れはネイティブウィンドウ操作を伴わないため、
    キーイベント内から同期的に呼んでも IME を乱さない。

    キー操作:
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
        # IME 変換途中（プリエディット中）かどうか。inputMethodEvent で更新する。
        # search_bar._FilterInput と同じ単純な追跡方式。
        self._composing: bool = False
        # 確定テキストがユーザー操作で変化したときにポップアップを更新する。
        # textEdited はプリエディット中（変換途中）は発火しないため、
        # 変換途中に誤ってポップアップが出てしまうことがない。
        self.textEdited.connect(self._on_text_edited)

    def set_all_tags(self, tags: list[str]) -> None:
        self._all_tags = tags

    def _ensure_popup(self) -> _CompletionPopup:
        if self._popup is None:
            self._popup = _CompletionPopup(self)
            self._popup.item_chosen.connect(self._on_item_clicked)
        return self._popup

    def _on_item_clicked(self, text: str) -> None:
        """ポップアップのアイテムをマウスクリックで選択したとき。"""
        if self._popup:
            self._popup.hide()
        self.clear()
        self.suggestion_confirmed.emit(text)

    def _update_popup(self) -> None:
        """
        現在の確定済み入力テキストに基づいてポップアップを更新する。
        プリエディット中は inputMethodEvent 側で更新自体を抑止する。
        """
        raw = self.text().strip()
        query = raw.lower()

        if not query:
            if self._popup:
                self._popup.hide()
            return

        matched = [t for t in self._all_tags if query in t.lower()]
        matches = sorted(matched, key=lambda t: (not t.lower().startswith(query), t.lower()))[:_MAX_SUGGESTIONS]
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

    def _on_text_edited(self, _text: str) -> None:
        """
        確定テキストがユーザー操作で変化したときに呼ばれる。

        ポップアップは子ウィジェット（非トップレベル）なので、show()/hide() が
        ネイティブウィンドウ操作を伴わず IME を乱さない。よって遅延（singleShot）を
        挟まず同期的に更新してよい。変換途中（プリエディット中）は確定テキストが
        まだ確定していないため更新しない。
        """
        if self._composing:
            return
        self._update_popup()

    def focusInEvent(self, event: QFocusEvent) -> None:
        super().focusInEvent(event)
        # 既存テキストの自動全選択だけを後段で打ち消す（Tab 等でフォーカスした際の
        # 「最初の IME 確定が全選択を置換する」問題を避ける）。
        #
        # 注: 「半角/全角での英数モード切替直後の先頭1文字落ち」は、MS-IME が
        # モード遷移中にその打鍵を消費する OS 側の挙動であり、Qt にイベントが
        # 一切届かないためアプリ側では阻止できない（調査で確定）。回避策は
        # 切替後に一瞬待ってから打つ、もしくは別の IME を使う。
        if event.reason() in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
        ) and self.hasSelectedText():
            self.deselect()
            self.setCursorPosition(len(self.text()))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        popup = self._popup
        key = event.key()

        # ─── Up/Down: ポップアップ即時表示 + ナビゲート ───────────────────────
        # Escape で一旦閉じた後などポップアップが非表示でも、確定テキストがあれば
        # 即座に表示してからナビゲートできるようにする。
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            if not popup or not popup.isVisible():
                raw = self.text().strip()
                if raw and not self._composing:
                    self._update_popup()
                    popup = self._popup
            if popup and popup.isVisible():
                popup.navigate(+1 if key == Qt.Key.Key_Down else -1)
                return
            # マッチなし / テキスト空 → 通常のカーソル移動に任せる
            super().keyPressEvent(event)
            return

        # ─── ポップアップ表示中の Enter / Escape ─────────────────────────────
        if popup and popup.isVisible():
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
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

        super().keyPressEvent(event)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        # 変換途中（プリエディット）かどうかを更新する。
        # keyPressEvent の Up/Down 即時表示判定で参照する。
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

        if self._composing:
            # 変換途中はポップアップを隠す（確定テキストはまだ変化していない）。
            # _CompletionPopup は子ウィジェットなので hide() は IME を乱さない。
            if self._popup:
                self._popup.hide()
            return

        # 変換が確定（プリエディットが空）したタイミングで確定テキストにより更新する。
        # 純粋な英数入力では textEdited 経由でも更新されるが、変換キャンセル時など
        # textEdited が発火しないケースに備えてここでも更新する（冪等）。
        self._update_popup()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # フォーカスが外れる際、システムは進行中の IME 変換を必ず終了させる。
        # 変換状態フラグが残っていると、再フォーカス後（例: IME モード切替で
        # 一旦ウィンドウが非アクティブになり戻ってきたとき）の一文字目で
        # 誤ってポップアップ更新が抑止されるため、ここで必ずクリアする。
        self._composing = False
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
        if not text or text in self._tags:
            self._input.clear()
            return
        # 既存タグでなければ新規作成確認ダイアログを表示
        if text not in self._input._all_tags:
            reply = QMessageBox.question(
                self,
                "新規タグの作成",
                f'「{text}」は新しいタグです。作成しますか？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
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
        sorted_tags = sorted(self._tags, key=lambda t: (self._color_map.get(t) or "~", t.lower()))
        for tag in sorted_tags:
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
