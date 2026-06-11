"""Star rating widget — clickable 0〜5 star selector."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

MAX_STARS = 5

_FILLED = "★"
_EMPTY  = "☆"


class StarRating(QWidget):
    """星 0〜5 を表すクリック可能なウィジェット。

    - 星 i をクリックすると評価が (i+1) になる。
    - 現在の評価と同じ星をクリックすると 0 にリセットされる。
    - ホバー中はその位置までの星をプレビュー表示する。

    rating_changed(int) はユーザー操作で値が変わったときのみ発火する
    （set_rating によるプログラム的な変更では発火しない）。
    """

    rating_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None, read_only: bool = False) -> None:
        super().__init__(parent)
        self._rating = 0
        self._hover = 0
        self._read_only = read_only

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._stars: list[QPushButton] = []
        for i in range(MAX_STARS):
            btn = QPushButton(_EMPTY)
            btn.setObjectName("starButton")
            btn.setFlat(True)
            btn.setCursor(Qt.CursorShape.ArrowCursor if read_only else Qt.CursorShape.PointingHandCursor)
            btn.setFixedSize(QSize(26, 26))
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if not read_only:
                btn.clicked.connect(lambda _checked=False, idx=i: self._on_clicked(idx))
                btn.installEventFilter(self)
            self._stars.append(btn)
            layout.addWidget(btn)
        layout.addStretch()

        self._refresh()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def rating(self) -> int:
        return self._rating

    def set_rating(self, value: int) -> None:
        """評価をプログラム的に設定する（シグナルは発火しない）。"""
        self._rating = max(0, min(MAX_STARS, int(value or 0)))
        self._hover = 0
        self._refresh()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_clicked(self, idx: int) -> None:
        new_rating = idx + 1
        if new_rating == self._rating:
            new_rating = 0  # 同じ星を再クリックでクリア
        self._rating = new_rating
        self._hover = 0
        self._refresh()
        self.rating_changed.emit(self._rating)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if self._read_only:
            return super().eventFilter(obj, event)
        if obj in self._stars:
            etype = event.type()
            if etype == event.Type.Enter:
                self._hover = self._stars.index(obj) + 1
                self._refresh()
            elif etype == event.Type.Leave:
                self._hover = 0
                self._refresh()
        return super().eventFilter(obj, event)

    def _refresh(self) -> None:
        active = self._hover if self._hover else self._rating
        for i, btn in enumerate(self._stars):
            btn.setText(_FILLED if i < active else _EMPTY)
            # ホバー中は強調色、通常は確定色
            on = i < active
            if self._hover and on:
                btn.setProperty("starState", "hover")
            elif on:
                btn.setProperty("starState", "on")
            else:
                btn.setProperty("starState", "off")
            btn.style().unpolish(btn)
            btn.style().polish(btn)
