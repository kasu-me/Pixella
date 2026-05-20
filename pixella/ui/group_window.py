"""Group window — shows a group's images in a separate grid window."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel, QMainWindow, QStatusBar, QVBoxLayout, QWidget,
)

from pixella.core import natural_sort_key
from pixella.db.models import Group, Image
from pixella.core.workers import ThumbnailWorkerPool
from pixella.ui.grid_view import ThumbnailGridWidget
from pixella.ui.sort_bar import SortBar


class GroupWindow(QMainWindow):
    """グループ内の画像をグリッド表示する非モーダルウィンドウ。
    グループに所属する画像へのタグ付けは行えない（グループ単位で管理）。
    """

    def __init__(
        self,
        group: Group,
        pool: ThumbnailWorkerPool,
        sort_key: str = "added",
        sort_desc: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._group_id = group.id
        self._sort_key = sort_key
        self._sort_desc = sort_desc
        self._images: list[Image] = list(group.images)
        self.setWindowTitle(f"⊞ {group.name}")
        self.resize(860, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        # ソートバー (一覧画面と同じキーを使用、保存しない)
        self._sort_bar = SortBar()
        self._sort_bar.blockSignals(True)
        valid_keys = ["added", "created", "name"]
        idx = valid_keys.index(sort_key) if sort_key in valid_keys else 0
        self._sort_bar._combo.setCurrentIndex(idx)
        self._sort_bar._dir_btn.setChecked(sort_desc)
        self._sort_bar.blockSignals(False)
        self._sort_bar.sort_changed.connect(self._on_sort_changed)
        layout.addWidget(self._sort_bar)

        self._grid = ThumbnailGridWidget(pool)
        self._grid.selection_changed.connect(self._on_selection_changed)
        layout.addWidget(self._grid, 1)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_label = QLabel()
        self._status.addWidget(self._status_label)

        self._apply_sort_and_load()

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _get_sort_key(self, img: Image):
        if self._sort_key == "added":
            return img.added_at or datetime.min
        elif self._sort_key == "created":
            return img.ctime or 0.0
        else:  # "name"
            return natural_sort_key(img.filename)

    def _apply_sort_and_load(self) -> None:
        sorted_images = sorted(self._images, key=self._get_sort_key, reverse=self._sort_desc)
        self._grid.load_items(sorted_images)
        self._status_label.setText(f"{len(sorted_images)} 枚")

    def _on_sort_changed(self, key: str, desc: bool) -> None:
        self._sort_key = key
        self._sort_desc = desc
        self._apply_sort_and_load()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self, items: list) -> None:
        if len(items) == 1 and isinstance(items[0], Image):
            self._status_label.setText(items[0].filename)
        elif len(items) > 1:
            self._status_label.setText(f"{len(items)} 枚選択中")
        else:
            self._status_label.setText(f"{self._grid.count()} 枚")

