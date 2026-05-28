"""Dialog for creating or renaming a group."""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QStyle, QVBoxLayout,
)

from pixella.core.workers import ThumbnailWorkerPool


class GroupDialog(QDialog):
    def __init__(self, image_names: list[str], default_name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("グループを作成")
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(default_name or "グループ")
        form.addRow("グループ名:", self._name_edit)

        member_label = QLabel("\n".join(image_names[:10]) + ("\n…" if len(image_names) > 10 else ""))
        member_label.setWordWrap(True)
        form.addRow("対象画像:", member_label)

        layout.addLayout(form)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    @property
    def group_name(self) -> str:
        return self._name_edit.text().strip() or "グループ"


class _CheckableListWidget(QListWidget):
    """チェックボックス以外の領域（サムネイル・ファイル名）をクリックしても
    チェック状態をトグルできる QListWidget サブクラス。"""

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        item = self.itemAt(event.pos())
        if item is not None and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            item_rect = self.visualItemRect(item)
            cb_w = self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth) + 8
            checkbox_rect = QRect(
                item_rect.left(), item_rect.top(), cb_w, item_rect.height()
            )
            if not checkbox_rect.contains(event.pos()):
                new_state = (
                    Qt.CheckState.Unchecked
                    if item.checkState() == Qt.CheckState.Checked
                    else Qt.CheckState.Checked
                )
                item.setCheckState(new_state)
                return
        super().mousePressEvent(event)


class RegexInputDialog(QDialog):
    """正規表現グループ化: 正規表現入力ダイアログ (ステップ 2-4)。"""

    def __init__(self, default_pattern: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正規表現グループ化")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._pattern_edit = QLineEdit(default_pattern)
        self._pattern_edit.setPlaceholderText("例: ^IMG_\\d+\\.jpg$")
        form.addRow("ファイル名の正規表現:", self._pattern_edit)

        layout.addLayout(form)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    @property
    def pattern(self) -> str:
        return self._pattern_edit.text()


class RegexGroupPreviewDialog(QDialog):
    """正規表現グループ化: マッチ結果確認・グループ名入力ダイアログ (ステップ 5-6)。"""

    def __init__(self, images: list, pool: ThumbnailWorkerPool, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("正規表現グループ化 — マッチ結果")
        self.setMinimumWidth(460)
        self.setMinimumHeight(440)

        self._id_to_item: dict[int, QListWidgetItem] = {}

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit("グループ")
        form.addRow("グループ名:", self._name_edit)
        layout.addLayout(form)

        count_label = QLabel(f"マッチした画像: {len(images)} 枚  （チェックした画像をグループ化します）")
        layout.addWidget(count_label)

        self._list = _CheckableListWidget()
        self._list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self._list.setIconSize(QSize(80, 80))
        self._list.setSpacing(2)
        self._list.setUniformItemSizes(True)
        for img in images:
            item = QListWidgetItem(img.filename)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, img)
            self._list.addItem(item)
            self._id_to_item[img.id] = item
            pool.request(img.id, img.path, self._on_thumb_done)
        layout.addWidget(self._list, 1)

        bbox = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _on_thumb_done(self, image_id: int, thumb_path: str) -> None:
        item = self._id_to_item.get(image_id)
        if item is not None:
            item.setIcon(QIcon(QPixmap(thumb_path)))

    @property
    def group_name(self) -> str:
        return self._name_edit.text().strip() or "グループ"

    @property
    def selected_images(self) -> list:
        result = []
        for i in range(self._list.count()):
            item = self._list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result
