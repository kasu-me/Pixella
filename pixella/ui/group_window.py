"""Group window — shows a group's images in a separate grid window."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QStatusBar, QVBoxLayout, QWidget,
)

from pixella.db.models import Group, Image
from pixella.core.workers import ThumbnailWorkerPool
from pixella.ui.grid_view import ThumbnailGridWidget


class GroupWindow(QMainWindow):
    """グループ内の画像をグリッド表示する非モーダルウィンドウ。
    グループに所属する画像へのタグ付けは行えない（グループ単位で管理）。
    """

    def __init__(
        self,
        group: Group,
        pool: ThumbnailWorkerPool,
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._group_id = group.id
        self.setWindowTitle(f"⊞ {group.name}")
        self.resize(860, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self._grid = ThumbnailGridWidget(pool)
        self._grid.selection_changed.connect(self._on_selection_changed)
        layout.addWidget(self._grid, 1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_label = QLabel()
        self._status.addWidget(self._status_label)

        images = sorted(group.images, key=lambda img: img.added_at or 0)
        self._grid.load_items(images)
        self._status_label.setText(f"{len(images)} 枚")

    def _on_selection_changed(self, items: list) -> None:
        if len(items) == 1 and isinstance(items[0], Image):
            self._status_label.setText(items[0].filename)
        elif len(items) > 1:
            self._status_label.setText(f"{len(items)} 枚選択中")
        else:
            self._status_label.setText(f"{self._grid.count()} 枚")
