"""Memo editor — 画像／グループの自由記述メモ（改行可）入力欄。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QTimer, Signal
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QWidget

from pixella.ui.ime_fix import ImeFirstCharRescue


class _OutsideInteractionFilter(QObject):
    """アプリ全体のマウス押下／ショートカット発火を監視し、メモ欄の外の操作なら通知する。"""

    def __init__(self, edit: "MemoEdit") -> None:
        super().__init__(edit)
        self._edit = edit

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt命名)
        t = event.type()
        if t == QEvent.Type.Shortcut:
            self._edit.commit()
        elif t == QEvent.Type.MouseButtonPress and isinstance(obj, QWidget):
            # QWindow 宛ての同イベントは対象外（続いて配送される QWidget 側で判定する）
            if not (obj is self._edit or self._edit.isAncestorOf(obj)):
                self._edit.commit()
        return False


class MemoEdit(ImeFirstCharRescue, QPlainTextEdit):
    """複数行のメモ入力欄。

    編集内容は「一定時間の無操作」「フォーカス喪失」「メモ欄の外へのクリック／
    ショートカット操作の直前」のいずれかで memo_committed により確定通知する。

    外部操作の直前に確定するのは、ツールバーボタンのようにフォーカスを奪わない操作
    （グループ化・アルバム切替など）が、未保存のメモを置き去りにしたまま DB を
    書き換えてしまうのを防ぐため。未確定の間だけアプリ全体にイベントフィルタを掛け、
    マウス押下（クリック処理は離した時点で走る）／ショートカット発火を先回りして検知する。
    """

    memo_committed = Signal(str)   # 確定したメモ本文

    _IDLE_MS = 800

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("memoEdit")
        self.setTabChangesFocus(True)
        self._composing: bool = False   # IME 変換途中か（ImeFirstCharRescue が参照）
        self._loading: bool = False     # set_memo() による内容設定中
        self._dirty: bool = False       # 未確定の編集があるか
        self._outside_filter = _OutsideInteractionFilter(self)
        self._filtering: bool = False

        self._idle = QTimer(self)
        self._idle.setSingleShot(True)
        self._idle.setInterval(self._IDLE_MS)
        self._idle.timeout.connect(self._on_idle)
        self.textChanged.connect(self._on_text_changed)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_memo(self, text: str) -> None:
        """内容をプログラム的に設定する（memo_committed は発火しない・未確定分は破棄）。"""
        self._idle.stop()
        self._dirty = False
        self._set_filtering(False)
        self._loading = True
        try:
            self.setPlainText(text or "")
        finally:
            self._loading = False

    def is_dirty(self) -> bool:
        return self._dirty

    def commit(self) -> None:
        """未確定の編集があれば memo_committed で確定通知する。"""
        if not self._dirty:
            return
        self._dirty = False
        self._idle.stop()
        self._set_filtering(False)
        self.memo_committed.emit(self.toPlainText())

    # ------------------------------------------------------------------
    # ImeFirstCharRescue 宿主インターフェース
    # ------------------------------------------------------------------

    def insert(self, text: str) -> None:
        self.insertPlainText(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        # WM_CHAR 取りこぼし対策: Qt が配送したこの文字を pending から除く
        self._rescue_on_key_press(event)
        super().keyPressEvent(event)

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        # WM_CHAR 取りこぼし対策: 変換確定で入った文字の重複 WM_CHAR を抑止する
        self._rescue_on_input_method(event)
        self._composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        # フォーカス喪失時はシステムが IME 変換を終了させるのでフラグを戻す
        self._composing = False
        self.commit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._idle.start()
        self._set_filtering(True)

    def _on_idle(self) -> None:
        # 変換途中は確定を待つ（プリエディットは toPlainText に含まれないため）
        if self._composing:
            self._idle.start()
            return
        self.commit()

    def _set_filtering(self, on: bool) -> None:
        if on == self._filtering:
            return
        app = QApplication.instance()
        if app is None:
            return
        if on:
            app.installEventFilter(self._outside_filter)
        else:
            app.removeEventFilter(self._outside_filter)
        self._filtering = on
