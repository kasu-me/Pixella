"""Grid view of image / group thumbnails."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Union

from PySide6.QtCore import Qt, Signal, QSize, QMimeData, QPoint, QUrl
from PySide6.QtGui import QPixmap, QIcon, QDrag, QPainter, QColor, QFont, QDesktopServices, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QListWidget, QListWidgetItem, QSizePolicy,
    QStyledItemDelegate,
)

from pixella.db.models import Image, Group
from pixella.core.workers import ThumbnailWorkerPool

THUMB_SIZE = 200
ITEM_SIZE  = 220

# Sentinel type alias
GridItem = Union[Image, Group]

# カスタムデータロール — タグの色リストを格納
TAG_COLORS_ROLE = Qt.ItemDataRole.UserRole + 1

_CHIP_D   = 12   # チップ直径 (px)
_CHIP_GAP = 3    # チップ間隔
_CHIP_ML  = 5    # アイテム左端からのマージン
_CHIP_PAD = 4    # 背景ストリップ内の左右パディング（均等）
_CHIP_MY  = 4    # 背景ストリップ内の上下パディング
_CHIP_MAX = 10   # 最大表示数
_TEXT_H   = 40   # テキストラベル＋余白の高さ（チップ描画位置の算出用）
_CHIP_DEFAULT_COLOR = "#dbeafe"  # 色未設定タグのデフォルト色（tagChip と統一）


class _TagChipDelegate(QStyledItemDelegate):
    """サムネイル左下にタグの色チップを重ねて描画するデリゲート。"""

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        super().paint(painter, option, index)
        colors: list[str | None] | None = index.data(TAG_COLORS_ROLE)
        if not colors:
            return

        r = option.rect
        n = min(len(colors), _CHIP_MAX)
        content_w = n * _CHIP_D + (n - 1) * _CHIP_GAP
        total_w   = content_w + _CHIP_PAD * 2
        total_h   = _CHIP_D + _CHIP_MY * 2

        # 背景ストリップの左端
        bg_x = r.left() + _CHIP_ML
        bg_y = r.bottom() - _TEXT_H - total_h + 1

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 半透明の背景ストリップ（左右均等パディング）
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 80))
        painter.drawRoundedRect(bg_x, bg_y, total_w, total_h, 4, 4)

        # 各タグの色チップ（円）
        cx = bg_x + _CHIP_PAD
        cy = bg_y + _CHIP_MY
        for color in colors[:n]:
            c = QColor(color) if color else QColor(_CHIP_DEFAULT_COLOR)
            painter.setBrush(c)
            painter.setPen(QColor(0, 0, 0, 70))
            painter.drawEllipse(cx, cy, _CHIP_D, _CHIP_D)
            cx += _CHIP_D + _CHIP_GAP

        painter.restore()


def _group_badge_pixmap(base: QPixmap) -> QPixmap:
    """Overlay a small 'stack' badge on the top-right corner."""
    result = QPixmap(base.size())
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, base)
    # Badge background
    badge_size = 32
    x = base.width() - badge_size - 4
    y = 4
    painter.setBrush(QColor(60, 120, 255, 220))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(x, y, badge_size, badge_size, 6, 6)
    # Badge icon text — pointSize を明示して -1 警告を回避
    font = QFont()
    font.setPointSize(12)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(x, y, badge_size, badge_size, Qt.AlignmentFlag.AlignCenter, "⊞")
    painter.end()
    return result


class ThumbnailGridWidget(QListWidget):
    """
    Displays images and groups as a responsive thumbnail grid.
    Supports multi-select and drag-and-drop.
    """

    item_activated = Signal(object)    # emits Image or Group
    selection_changed = Signal(list)   # emits list[GridItem]
    drop_files = Signal(list)          # emits list[str] (file paths)

    def __init__(self, pool: ThumbnailWorkerPool, parent=None) -> None:
        super().__init__(parent)
        self._pool = pool
        self._id_to_item: dict[str, QListWidgetItem] = {}  # key: "img:{id}" or "grp:{id}"
        self._placeholder = QPixmap(THUMB_SIZE, THUMB_SIZE)
        self._placeholder.fill(QColor(180, 180, 180, 80))

        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.setGridSize(QSize(ITEM_SIZE, ITEM_SIZE + 24))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Static)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setSpacing(4)

        # DnD: accept files dropped from Explorer
        # viewport() 自体にも acceptDrops を設定することでイベントがピックアップされる
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(False)  # internal drag disabled intentionally
        self.setDropIndicatorShown(False)

        # サムネイル遅延リクエスト用の状態
        self._thumb_requested: set[str] = set()             # リクエスト済みキー
        self._cover_id_to_grp_key: dict[int, str] = {}      # cover_image_id → "grp:{id}"

        self.setItemDelegate(_TagChipDelegate(self))

        self.itemActivated.connect(self._on_activated)
        self.itemSelectionChanged.connect(self._on_selection_changed)
        self.verticalScrollBar().valueChanged.connect(self._request_visible_range)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_items(self, items: list[GridItem]) -> None:
        self._thumb_requested.clear()
        self._cover_id_to_grp_key.clear()
        # 更新検知・レイアウト再計算を一括化してロード時間を削減
        self.setUpdatesEnabled(False)
        self.blockSignals(True)
        try:
            self.clear()
            self._id_to_item.clear()
            for item in items:
                self._add_item(item)
        finally:
            self.blockSignals(False)
            self.setUpdatesEnabled(True)
        # 表示範囲のサムネイルだけを非同期リクエスト
        self._request_visible_range()

    def update_thumb(self, image_id: int, thumb_path: str) -> None:
        px_base: QPixmap | None = None

        # 画像アイテム更新
        lw_item = self._id_to_item.get(f"img:{image_id}")
        if lw_item:
            px_base = QPixmap(thumb_path).scaled(
                THUMB_SIZE, THUMB_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            lw_item.setIcon(QIcon(px_base))

        # グループカバー更新 — O(1) ハッシュ検索
        grp_key = self._cover_id_to_grp_key.get(image_id)
        if grp_key:
            lw_item2 = self._id_to_item.get(grp_key)
            if lw_item2:
                if px_base is None:
                    px_base = QPixmap(thumb_path).scaled(
                        THUMB_SIZE, THUMB_SIZE,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                lw_item2.setIcon(QIcon(_group_badge_pixmap(px_base)))

    def selected_items_data(self) -> list[GridItem]:
        return [i.data(Qt.ItemDataRole.UserRole) for i in self.selectedItems()]

    def set_item_tags(self, data: GridItem) -> None:
        """タグ更新後にチップ色だけを再描画させる（フル再ロード不要）。"""
        key = f"img:{data.id}" if isinstance(data, Image) else f"grp:{data.id}"
        lw_item = self._id_to_item.get(key)
        if lw_item:
            lw_item.setData(TAG_COLORS_ROLE, [t.color for t in data.tags])
            self.update(self.indexFromItem(lw_item))

    def update_tag_colors(self, color_map: dict[str, str | None]) -> None:
        """タグ色マップが変更された際に全グリッドアイテムのチップ色を一括更新する。"""
        for lw_item in self._id_to_item.values():
            data = lw_item.data(Qt.ItemDataRole.UserRole)
            if data is None:
                continue
            colors = [
                color_map.get(t.name, t.color)
                for t in data.tags
            ]
            lw_item.setData(TAG_COLORS_ROLE, colors)
        self.viewport().update()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _add_item(self, data: GridItem) -> None:
        if isinstance(data, Image):
            key = f"img:{data.id}"
            label = data.filename
        else:
            key = f"grp:{data.id}"
            label = f"⊞ {data.name}"
            # カバー画像 → グループキーの逆引きインデックスを構築
            if data.cover_image:
                self._cover_id_to_grp_key[data.cover_image.id] = key

        lw_item = QListWidgetItem(QIcon(self._placeholder), label)
        lw_item.setData(Qt.ItemDataRole.UserRole, data)
        lw_item.setData(TAG_COLORS_ROLE, [t.color for t in data.tags])
        lw_item.setSizeHint(QSize(ITEM_SIZE, ITEM_SIZE + 24))
        self.addItem(lw_item)
        self._id_to_item[key] = lw_item
        # サムネイルリクエストは _request_visible_range() で遅延実行

    def _on_activated(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, Image):
            # 画像は OS 既定アプリで開く
            QDesktopServices.openUrl(QUrl.fromLocalFile(data.path))
        else:
            # グループは item_activated で親に委譲
            self.item_activated.emit(data)

    def _on_selection_changed(self) -> None:
        self.selection_changed.emit(self.selected_items_data())

    # ------------------------------------------------------------------
    # Visible-range thumbnail loading
    # ------------------------------------------------------------------

    def _visible_range(self) -> tuple[int, int]:
        """現在ビューポートに表示されているアイテムの (first, last) インデックスを返す。"""
        n = self.count()
        if n == 0:
            return (0, -1)
        vp_h = self.viewport().height()

        # 二分探索で最初の可視アイテムを探す
        lo, hi = 0, n - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.visualItemRect(self.item(mid)).bottom() < 0:
                lo = mid + 1
            else:
                hi = mid
        first = lo
        if self.visualItemRect(self.item(first)).bottom() < 0:
            return (0, -1)

        # 二分探索で最後の可視アイテムを探す
        lo, hi = first, n - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.visualItemRect(self.item(mid)).top() <= vp_h:
                lo = mid
            else:
                hi = mid - 1
        return (first, lo)

    def _request_visible_range(self) -> None:
        """表示範囲 ± バッファ分のアイテムだけサムネイルをリクエストする。"""
        first, last = self._visible_range()
        if last < first:
            return
        buf = 20
        start = max(0, first - buf)
        end   = min(self.count() - 1, last + buf)
        for i in range(start, end + 1):
            item = self.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, Image):
                key = f"img:{data.id}"
                if key not in self._thumb_requested:
                    self._thumb_requested.add(key)
                    self._pool.request(data.id, data.path, self.update_thumb)
            else:
                key = f"grp:{data.id}"
                if key not in self._thumb_requested and data.cover_image:
                    self._thumb_requested.add(key)
                    self._pool.request(data.cover_image.id, data.cover_image.path, self.update_thumb)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._request_visible_range()

    # ------------------------------------------------------------------
    # Wheel scroll — 1ステップあたりのスクロール量を抑制
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        # Qt デフォルトは angleDelta 120 単位で3行スクロール。
        # ここでは 1 ステップ = アイテム高の約 1/3 相当に抑える。
        delta = event.angleDelta().y()
        step = self.gridSize().height() // 3  # ≈ 81px (ITEM_SIZE=220+24=244 → 81)
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() - (step if delta > 0 else -step))
        event.accept()

    # ------------------------------------------------------------------
    # Drag-and-drop (file drop from Explorer)
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            paths = [
                u.toLocalFile()
                for u in event.mimeData().urls()
                if u.isLocalFile()
            ]
            if paths:
                self.drop_files.emit(paths)
            event.acceptProposedAction()
