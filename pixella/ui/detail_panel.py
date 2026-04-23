"""Detail panel — shown on the right when an image or group is selected."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout, QInputDialog, QLabel, QScrollArea, QSizePolicy,
    QVBoxLayout, QWidget, QPushButton, QHBoxLayout, QFrame,
)

from pixella.db.models import Image, Group
from pixella.ui.tag_input import TagInputWidget


class _SectionTitle(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("sectionTitle")


class DetailPanel(QWidget):
    """Right-side panel showing metadata and tags for the selected item."""

    tags_committed    = Signal(list)        # list[str] — 1アイテム選択時のタグリスト
    multi_tag_added   = Signal(list, str)     # (list[Image|Group], tag_name)  — 複数選択時
    multi_tag_removed = Signal(list, str)     # (list[Image|Group], tag_name)  — 複数選択時
    open_group        = Signal(object)         # Group
    remove_from_group = Signal(object)         # Image
    group_renamed     = Signal(object, str)    # (Group, new_name)
    tags_copy_requested  = Signal()            # タグをコピーボタン
    tags_paste_requested = Signal()            # タグを貼り付けボタン

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._current: Image | Group | None = None
        self._current_multi: list[Image] = []
        self._current_multi_groups: list[Group] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Preview
        self._preview = QLabel()
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setFixedHeight(240)
        self._preview.setObjectName("detailPreview")
        layout.addWidget(self._preview)

        # Info
        self._info_label = QLabel()
        self._info_label.setWordWrap(True)
        self._info_label.setObjectName("detailInfo")
        layout.addWidget(self._info_label)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setObjectName("divider")
        layout.addWidget(line)

        # Tags section
        layout.addWidget(_SectionTitle("タグ"))
        # タグ操作ボタンバー（コピー・貼り付け）
        tag_btn_bar = QWidget()
        tag_btn_layout = QHBoxLayout(tag_btn_bar)
        tag_btn_layout.setContentsMargins(0, 0, 0, 0)
        tag_btn_layout.setSpacing(4)
        self._btn_copy_tags = QPushButton("タグをコピー")
        self._btn_copy_tags.setToolTip("選択中のアイテムのタグをコピーします")
        self._btn_copy_tags.clicked.connect(self.tags_copy_requested)
        self._btn_paste_tags = QPushButton("タグを貼り付け")
        self._btn_paste_tags.setToolTip("コピーしたタグを現在の選択に追加します")
        self._btn_paste_tags.setEnabled(False)
        self._btn_paste_tags.clicked.connect(self.tags_paste_requested)
        tag_btn_layout.addWidget(self._btn_copy_tags)
        tag_btn_layout.addWidget(self._btn_paste_tags)
        tag_btn_layout.addStretch()
        layout.addWidget(tag_btn_bar)
        self._tag_input = TagInputWidget()
        self._tag_input.tags_changed.connect(self._on_tags_changed)
        self._tag_input.tag_added.connect(self._on_tag_added)
        self._tag_input.tag_removed.connect(self._on_tag_removed)
        layout.addWidget(self._tag_input)

        # グループ所属画像の場合に表示する警告ノート
        self._grouped_note = QLabel("ℹ この画像はグループに所属しています。\nタグはグループ導体で管理されます。")
        self._grouped_note.setObjectName("groupedNote")
        self._grouped_note.setWordWrap(True)
        self._grouped_note.setVisible(False)
        layout.addWidget(self._grouped_note)

        # Group info section (for images)
        self._group_section = QWidget()
        gs_layout = QVBoxLayout(self._group_section)
        gs_layout.setContentsMargins(0, 0, 0, 0)
        gs_layout.addWidget(_SectionTitle("グループ"))
        self._group_label = QLabel("(なし)")
        self._group_label.setWordWrap(True)
        gs_layout.addWidget(self._group_label)

        self._open_group_btn = QPushButton("グループを開く")
        self._open_group_btn.setVisible(False)
        self._open_group_btn.clicked.connect(self._on_open_group)
        gs_layout.addWidget(self._open_group_btn)

        self._remove_from_group_btn = QPushButton("グループから外す")
        self._remove_from_group_btn.setVisible(False)
        self._remove_from_group_btn.clicked.connect(self._on_remove_from_group)
        gs_layout.addWidget(self._remove_from_group_btn)

        layout.addWidget(self._group_section)

        # Group rename section (グループ単体選択時)
        self._group_rename_section = QWidget()
        gr_layout = QVBoxLayout(self._group_rename_section)
        gr_layout.setContentsMargins(0, 0, 0, 0)
        self._rename_group_btn = QPushButton("グループ名を変更…")
        self._rename_group_btn.clicked.connect(self._on_rename_group)
        gr_layout.addWidget(self._rename_group_btn)
        self._group_rename_section.setVisible(False)
        layout.addWidget(self._group_rename_section)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def set_completion_list(self, tags: list[str]) -> None:
        self._tag_input.set_completion_list(tags)

    def set_color_map(self, color_map: dict[str, str | None]) -> None:
        self._tag_input.set_color_map(color_map)

    def set_clipboard_available(self, available: bool) -> None:
        """タグクリップボードに内容があるかどうかで貼り付けボタンの有効/無効を切り替える。"""
        self._btn_paste_tags.setEnabled(available)

    def show_image(self, image: Image) -> None:
        self._current = image
        self._current_multi = []
        self._current_multi_groups = []
        self._group_rename_section.setVisible(False)
        self._load_preview(image.path)
        p = Path(image.path)
        self._info_label.setText(
            f"<b>{p.name}</b><br>"
            f"<small>{p.parent}</small>"
        )

        # グループ所属画像はタグ編集不可
        if image.group:
            self._tag_input.setVisible(False)
            self._grouped_note.setVisible(True)
        else:
            self._tag_input.setVisible(True)
            self._grouped_note.setVisible(False)
            self._tag_input.set_tags([t.name for t in image.tags])

        # group info
        self._group_section.setVisible(True)
        if image.group:
            self._group_label.setText(f"<b>{image.group.name}</b>")
            self._open_group_btn.setVisible(True)
            self._remove_from_group_btn.setVisible(True)
        else:
            self._group_label.setText("(グループなし)")
            self._open_group_btn.setVisible(False)
            self._remove_from_group_btn.setVisible(False)

    def show_multi_images(self, images: list[Image], groups: list[Group] | None = None) -> None:
        """複数アイテム選択時の表示。タグ入力には全アイテムの共通タグを表示する。"""
        self._current = None
        self._current_multi = images
        self._current_multi_groups = groups or []
        self._preview.clear()
        self._tag_input.setVisible(True)
        self._grouped_note.setVisible(False)
        n_img = len(images)
        n_grp = len(self._current_multi_groups)
        parts = []
        if n_img:
            parts.append(f"{n_img} 枚")
        if n_grp:
            parts.append(f"グループ {n_grp} 件")
        self._info_label.setText(f"<b>{'・'.join(parts)} 選択中</b><br><small>共通タグを編集できます</small>")
        # 全アイテムの共通タグ（AND）を表示
        all_items: list[Image | Group] = list(images) + list(self._current_multi_groups)
        if all_items:
            common = set(t.name for t in all_items[0].tags)
            for item in all_items[1:]:
                common &= {t.name for t in item.tags}
        else:
            common = set()
        self._tag_input.set_tags(sorted(common))
        self._group_section.setVisible(False)

    def show_group(self, group: Group) -> None:
        self._current = group
        self._current_multi = []
        self._current_multi_groups = []
        # Show cover image preview
        if group.cover_image:
            self._load_preview(group.cover_image.path)
        else:
            self._preview.clear()
        self._info_label.setText(
            f"<b>⊞ {group.name}</b><br>"
            f"<small>{len(group.images)} 枚</small>"
        )
        self._tag_input.setVisible(True)
        self._grouped_note.setVisible(False)
        self._tag_input.set_tags([t.name for t in group.tags])
        self._group_section.setVisible(False)
        self._group_rename_section.setVisible(True)

    def clear(self) -> None:
        self._current = None
        self._current_multi = []
        self._current_multi_groups = []
        self._preview.clear()
        self._info_label.clear()
        self._tag_input.set_tags([])
        self._tag_input.setVisible(True)
        self._grouped_note.setVisible(False)
        self._group_section.setVisible(False)
        self._group_rename_section.setVisible(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_preview(self, path: str) -> None:
        px = QPixmap(path)
        if px.isNull():
            self._preview.setText("プレビュー不可")
        else:
            self._preview.setPixmap(
                px.scaled(
                    self._preview.width() or 240,
                    240,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )

    def _on_tags_changed(self, tags: list[str]) -> None:
        # 単一項目選択時のみ使用（複数選択時は tag_added / tag_removed 経由）
        if self._current_multi or self._current_multi_groups:
            return
        if self._current is not None:
            # item は main_window 側がグリッドの実選択から取得するため、ここでは tags のみ送出
            self.tags_committed.emit(tags)

    def _on_tag_added(self, tag: str) -> None:
        if self._current_multi or self._current_multi_groups:
            self.multi_tag_added.emit(self._current_multi + self._current_multi_groups, tag)

    def _on_tag_removed(self, tag: str) -> None:
        if self._current_multi or self._current_multi_groups:
            self.multi_tag_removed.emit(self._current_multi + self._current_multi_groups, tag)

    def _on_open_group(self) -> None:
        if isinstance(self._current, Image) and self._current.group:
            self.open_group.emit(self._current.group)

    def _on_remove_from_group(self) -> None:
        if isinstance(self._current, Image):
            self.remove_from_group.emit(self._current)

    def _on_rename_group(self) -> None:
        if not isinstance(self._current, Group):
            return
        new_name, ok = QInputDialog.getText(
            self,
            "グループ名を変更",
            "新しいグループ名:",
            text=self._current.name,
        )
        if ok and new_name.strip():
            self.group_renamed.emit(self._current, new_name.strip())
