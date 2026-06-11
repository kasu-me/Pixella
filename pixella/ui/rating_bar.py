"""Rating filter bar — フィルタ用のレーティング絞り込みコントロール。"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from pixella.ui.star_rating import StarRating


class RatingFilterBar(QWidget):
    """星の数と比較演算子（≧ / = / ≦）でレーティングを絞り込むバー。

    filter_changed(enabled: bool, op: str, value: int) を発火する。
    op は ">=" / "==" / "<=" のいずれか。
    """

    filter_changed = Signal(bool, str, int)

    _OPS = [
        ("≧", ">="),
        ("=",  "=="),
        ("≦", "<="),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ratingBar")
        self._enabled = False
        self._op = ">="

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(6)

        lbl = QLabel("レーティング:")
        lbl.setObjectName("ratingBarLabel")
        layout.addWidget(lbl)

        # 有効/無効トグル
        self._enable_btn = QPushButton("絞り込み")
        self._enable_btn.setObjectName("ratingEnableBtn")
        self._enable_btn.setCheckable(True)
        self._enable_btn.setToolTip("レーティングによる絞り込みのオン/オフ")
        self._enable_btn.toggled.connect(self._on_enable_toggled)
        layout.addWidget(self._enable_btn)

        # 演算子トグル (≧ / = / ≦)
        op_frame = QFrame()
        op_frame.setObjectName("modeToggleFrame")
        op_layout = QHBoxLayout(op_frame)
        op_layout.setContentsMargins(1, 1, 1, 1)
        op_layout.setSpacing(0)
        self._op_btns: dict[str, QPushButton] = {}
        for label, op in self._OPS:
            btn = QPushButton(label)
            btn.setObjectName("ratingOpBtn")
            btn.setCheckable(True)
            btn.setFixedWidth(38)
            btn.setChecked(op == self._op)
            btn.clicked.connect(lambda _checked=False, o=op: self._on_op_clicked(o))
            self._op_btns[op] = btn
            op_layout.addWidget(btn)
        layout.addWidget(op_frame)

        # 星選択
        self._stars = StarRating()
        self._stars.set_rating(0)
        self._stars.rating_changed.connect(self._on_value_changed)
        layout.addWidget(self._stars)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._enabled

    @property
    def op(self) -> str:
        return self._op

    @property
    def value(self) -> int:
        return self._stars.rating

    def reset(self) -> None:
        """絞り込みを無効化する（シグナルは発火しない）。"""
        self._enabled = False
        self._enable_btn.blockSignals(True)
        self._enable_btn.setChecked(False)
        self._enable_btn.blockSignals(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_enable_toggled(self, checked: bool) -> None:
        self._enabled = checked
        self._emit()

    def _on_op_clicked(self, op: str) -> None:
        self._op = op
        for o, btn in self._op_btns.items():
            btn.setChecked(o == op)
        if self._enabled:
            self._emit()

    def _on_value_changed(self, _value: int) -> None:
        # 星を操作したら自動的に絞り込みを有効化する
        if not self._enabled:
            self._enabled = True
            self._enable_btn.blockSignals(True)
            self._enable_btn.setChecked(True)
            self._enable_btn.blockSignals(False)
        self._emit()

    def _emit(self) -> None:
        self.filter_changed.emit(self._enabled, self._op, self._stars.rating)
