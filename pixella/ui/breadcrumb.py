"""Breadcrumb navigation bar displayed above the grid."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget


class BreadcrumbBar(QWidget):
    """
    Shows ホーム (> current context) above the thumbnail grid.
    Clicking "ホーム" when a child context is active emits `home_clicked`.
    """

    home_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("breadcrumbBar")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 4, 6, 4)
        self._layout.setSpacing(6)
        self._render(None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_home(self) -> None:
        """ホーム状態（ルート一覧）"""
        self._render(None)

    def set_group(self, name: str) -> None:
        """グループ内ドリルイン状態"""
        self._render(name)

    def set_search(self, query: str) -> None:
        """検索結果表示状態"""
        self._render(f"検索結果: {query}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _render(self, current: str | None) -> None:
        # 既存ウィジェットを破棄
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if current is None:
            # ルート: クリック不可のプレーンテキスト
            lbl = QLabel("ホーム")
            lbl.setObjectName("breadcrumbCurrent")
            self._layout.addWidget(lbl)
        else:
            # 子コンテキスト: 「ホーム」はリンク
            home = QLabel('<a href="#">ホーム</a>')
            home.setObjectName("breadcrumbLink")
            home.linkActivated.connect(lambda _: self.home_clicked.emit())
            self._layout.addWidget(home)

            sep = QLabel("›")
            sep.setObjectName("breadcrumbSep")
            self._layout.addWidget(sep)

            cur = QLabel(current)
            cur.setObjectName("breadcrumbCurrent")
            self._layout.addWidget(cur)

        self._layout.addStretch()
