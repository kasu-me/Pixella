"""Windows IME「先頭文字落ち」の恒久対策。

調査で判明した真因: 文字落ちは OS が打鍵を消費しているのではなく、**Qt(Windows
プラグイン)がウィンドウに届いた WM_CHAR を QKeyEvent に変換せず取りこぼしている**。
IME のモード切替(半角/全角)や変換キャンセルの直後など、QWindowsInputContext の
合成状態が不整合になると、直後の WM_CHAR を「変換結果の重複」と誤認して無視する。
(3層プローブで WM_CHAR がウィンドウに届いているのに KeyPress が出ないことを確認済み)

本モジュールは QAbstractNativeEventFilter で WM_CHAR を監視し、対象 QLineEdit が
その文字を実際に入力として受け取れたか(keyPressEvent / IME 変換確定)を照合する。
Qt が取りこぼした分だけを次のイベントループで補って挿入する。Qt が正常配送した文字や
IME 変換確定で既に入った文字は二重入力しないよう除外する。Windows 以外では何もしない。
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtWidgets import QApplication

_WM_CHAR = 0x0102

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD), ("pt_x", wintypes.LONG), ("pt_y", wintypes.LONG),
        ]


class ImeFirstCharRescue:
    """WM_CHAR 取りこぼし対策を受ける QLineEdit 用ミックスイン。

    使い方:  ``class MyEdit(ImeFirstCharRescue, QLineEdit): ...``
    宿主は変換中フラグ ``_composing``(bool) を持つこと。keyPressEvent /
    inputMethodEvent を override する場合は、それぞれの先頭で
    ``self._rescue_on_key_press(event)`` / ``self._rescue_on_input_method(event)``
    を呼ぶこと。
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._rescue_pending: list[str] = []   # WM_CHAR で届いたが未配送の文字(FIFO)
        self._rescue_commit_guard: str = ""    # IME 変換確定直後の重複 WM_CHAR 抑止用

    # ── ネイティブフィルタから呼ばれる（WM_CHAR 受信） ──────────────────
    def _note_wm_char(self, ch: str) -> None:
        if getattr(self, "_composing", False):
            return  # 変換中の WM_CHAR は扱わない（変換確定経路に任せる）
        if self._rescue_commit_guard and self._rescue_commit_guard[0] == ch:
            # 直前の IME 変換確定で入った文字の重複 WM_CHAR → 無視（二重入力防止）
            self._rescue_commit_guard = self._rescue_commit_guard[1:]
            return
        self._rescue_pending.append(ch)
        # 二段 singleShot(0) で flush を遅延させる。
        # 通常の英数入力では、Qt は文字を WM_KEYDOWN 段階で KeyPress として post し、
        # その KeyPress が実際に配送されるのは「次のループ反復の先頭」。一段の
        # singleShot(0) はそれより前のタイマーフェーズで発火してしまい、KeyPress で
        # pop される前に flush して二重入力になる。二段にすることで、KeyPress 配送後に
        # flush させ、正常配送の文字は二重にせず、取りこぼしだけを補う。
        QTimer.singleShot(0, lambda: QTimer.singleShot(0, self._rescue_flush))

    def _rescue_flush(self) -> None:
        if not self._rescue_pending:
            return
        chars = "".join(self._rescue_pending)
        self._rescue_pending.clear()
        # ここまでで pending に残った文字 = Qt が KeyPress としても変換確定としても
        # 配送しなかった文字（＝取りこぼし）。フォーカスがあり変換中でなければ補う。
        if self.hasFocus() and not getattr(self, "_composing", False):
            self.insert(chars)

    # ── 宿主の override から先頭で呼ぶフック ───────────────────────────
    def _rescue_on_key_press(self, event) -> None:
        # Qt が正常配送した文字を pending から取り除く（取りこぼしではない）
        t = event.text()
        if t and self._rescue_pending and self._rescue_pending[0] == t:
            self._rescue_pending.pop(0)

    def _rescue_on_input_method(self, event) -> None:
        commit = event.commitString()
        if commit:
            # 変換確定で入った文字は、直後に来る重複 WM_CHAR を抑止する
            self._rescue_commit_guard += commit
            QTimer.singleShot(0, self._clear_commit_guard)
            for c in commit:
                if self._rescue_pending and self._rescue_pending[0] == c:
                    self._rescue_pending.pop(0)

    def _clear_commit_guard(self) -> None:
        self._rescue_commit_guard = ""


class _WmCharRescueFilter(QAbstractNativeEventFilter):
    """全 WM_CHAR を監視し、フォーカス中のレスキュー対応 QLineEdit に通知する。"""

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt命名)
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
            if msg.message == _WM_CHAR:
                note = getattr(QApplication.focusWidget(), "_note_wm_char", None)
                if note is not None:
                    wp = int(msg.wParam)
                    # 印字可能のみ（制御文字・サロゲートペアは除外）
                    if 0x20 <= wp <= 0x10FFFF and not (0xD800 <= wp <= 0xDFFF):
                        note(chr(wp))
        except Exception:
            pass
        return False


_filter = None


def install_ime_fix(app: QApplication) -> None:
    """WM_CHAR レスキューを取り付ける（Windows のみ）。"""
    global _filter
    if sys.platform != "win32" or _filter is not None:
        return
    _filter = _WmCharRescueFilter()
    app.installNativeEventFilter(_filter)
