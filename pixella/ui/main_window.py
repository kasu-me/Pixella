"""Main application window."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QSizePolicy,
    QSplitter, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from pixella import __app_name__, __version__
from pixella.core import ThumbnailCache, ThumbnailWorkerPool, THUMB_DIR, SUPPORTED_EXTS, AlbumManager, natural_sort_key
from pixella.db import (
    get_session, init_db, all_images, all_groups, all_tag_names, all_tag_color_map, all_tags_with_count,
    add_images, images_without_tags, groups_without_tags, create_group, merge_groups, rename_group, dissolve_group,
    remove_image_from_group, set_image_tags, set_group_tags,
    export_json, export_json_combined, import_json,
    bulk_apply_tag_delta, bulk_apply_group_tag_delta,
    cleanup_uncolored_orphan_tags, _do_import, _validate_import_json,
)
from pixella.db.models import Image, Group, Tag
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pixella.ui.grid_view import ThumbnailGridWidget
from pixella.ui.detail_panel import DetailPanel
from pixella.ui.search_bar import SearchBar
from pixella.ui.dialogs import GroupDialog, RegexInputDialog, RegexGroupPreviewDialog
from pixella.ui.themes import apply_theme
from pixella.ui.breadcrumb import BreadcrumbBar
from pixella.ui.sort_bar import SortBar
from pixella.ui.tag_manager import TagManagerDialog
from pixella.ui.group_window import GroupWindow


class MainWindow(QMainWindow):
    def __init__(self, album_manager: AlbumManager) -> None:
        super().__init__()
        self._album_manager = album_manager
        self._dark_mode = False
        self._sort_key_name: str = "added"
        self._sort_desc: bool = False
        self._cache = ThumbnailCache(THUMB_DIR)
        self._pool = ThumbnailWorkerPool(self._cache)
        self._cached_images: list = []   # DB再クエリなしでソート変更を可能にするキャッシュ
        self._cached_groups: list = []
        self._group_windows: dict[int, GroupWindow] = {}  # group.id -> GroupWindow
        self._tag_clipboard: list[str] = []  # タグコピー用クリップボード
        # 現在のトップレベルビュー状態
        self._view_mode: str = "home"
        self._view_search_tags: list[str] = []
        self._view_search_mode: str = "and"

        self.setWindowTitle(f"{__app_name__} {__version__}")
        self._build_ui()
        self._build_menu()
        self._restore_geometry()
        self._restore_sort()
        self._init_album_combo()
        self._refresh_grid()

    # ------------------------------------------------------------------
    # Window geometry persistence
    # ------------------------------------------------------------------

    def _settings(self) -> QSettings:
        return QSettings("Pixella", "Pixella")

    def _restore_geometry(self) -> None:
        s = self._settings()
        geometry = s.value("window/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 760)

    def _sort_prefix(self) -> str:
        """現在のアルバムに対応する QSettings キーのプレフィックスを返す。"""
        return f"sort_album/{self._album_manager.active_db_key()}"

    def _save_sort(self) -> None:
        """現在のアルバムのソート設定を QSettings に保存する。"""
        s = self._settings()
        prefix = self._sort_prefix()
        s.setValue(f"{prefix}/key",  self._sort_key_name)
        s.setValue(f"{prefix}/desc", self._sort_desc)

    def _restore_sort(self) -> None:
        s = self._settings()
        prefix = self._sort_prefix()
        key  = s.value(f"{prefix}/key",  "added")
        desc = s.value(f"{prefix}/desc", False)
        # QSettings は文字列で返る場合があるので bool に変換
        if isinstance(desc, str):
            desc = desc.lower() == "true"
        self._sort_key_name = key
        self._sort_desc = bool(desc)
        # SortBar UI に反映（シグナルを一時ブロックして不要な再描画を防ぐ）
        self._sort_bar.blockSignals(True)
        valid_keys = ["added", "created", "name"]
        idx = valid_keys.index(key) if key in valid_keys else 0
        self._sort_bar._combo.setCurrentIndex(idx)
        self._sort_bar._dir_btn.setChecked(self._sort_desc)
        self._sort_bar.blockSignals(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        s = self._settings()
        s.setValue("window/geometry", self.saveGeometry())
        self._save_sort()
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Toolbar
        toolbar = QToolBar("メイン")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        # --- アルバムセレクター ---
        album_container = QWidget()
        album_layout = QHBoxLayout(album_container)
        album_layout.setContentsMargins(4, 2, 4, 2)
        album_layout.setSpacing(4)
        album_lbl = QLabel("アルバム:")
        album_layout.addWidget(album_lbl)
        self._album_combo = QComboBox()
        self._album_combo.setMinimumWidth(130)
        self._album_combo.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._album_combo.setToolTip("アルバムを切り替えます")
        album_layout.addWidget(self._album_combo)
        toolbar.addWidget(album_container)
        toolbar.addSeparator()

        self._act_add = QAction("画像を追加", self)
        self._act_add.setShortcut(QKeySequence("Ctrl+O"))
        self._act_add.triggered.connect(self._open_images)
        toolbar.addAction(self._act_add)

        toolbar.addSeparator()

        self._act_group = QAction("グループ化", self)
        self._act_group.setShortcut(QKeySequence("Ctrl+G"))
        self._act_group.setEnabled(False)
        self._act_group.triggered.connect(self._on_group_action)
        toolbar.addAction(self._act_group)

        self._act_regex_group = QAction("正規表現グループ化", self)
        self._act_regex_group.setToolTip("正規表現でファイル名を指定してグループ化します")
        self._act_regex_group.triggered.connect(self._on_regex_group_action)
        toolbar.addAction(self._act_regex_group)

        self._act_dissolve = QAction("グループ解除", self)
        self._act_dissolve.setEnabled(False)
        self._act_dissolve.triggered.connect(self._dissolve_group)
        toolbar.addAction(self._act_dissolve)

        toolbar.addSeparator()

        self._act_remove = QAction("削除", self)
        self._act_remove.setShortcut(QKeySequence.StandardKey.Delete)
        self._act_remove.setEnabled(False)
        self._act_remove.triggered.connect(self._remove_selected)
        toolbar.addAction(self._act_remove)

        self._act_regen_thumb = QAction("サムネイル再生成", self)
        self._act_regen_thumb.setEnabled(False)
        self._act_regen_thumb.triggered.connect(self._regen_selected_thumbs)
        toolbar.addAction(self._act_regen_thumb)

        toolbar.addSeparator()

        self._act_theme = QAction("ダークモード", self)
        self._act_theme.setCheckable(True)
        self._act_theme.triggered.connect(self._toggle_theme)
        toolbar.addAction(self._act_theme)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Search bar
        self._search_bar = SearchBar()
        self._search_bar.search_requested.connect(self._do_search)
        self._search_bar.cleared.connect(self._refresh_grid)
        self._search_bar.untagged_requested.connect(self._show_untagged)
        main_layout.addWidget(self._search_bar)

        # Sort bar
        self._sort_bar = SortBar()
        self._sort_bar.sort_changed.connect(self._on_sort_changed)
        main_layout.addWidget(self._sort_bar)

        # Breadcrumb bar
        self._breadcrumb = BreadcrumbBar()
        self._breadcrumb.home_clicked.connect(self._go_back)
        main_layout.addWidget(self._breadcrumb)

        # Splitter: grid | detail
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(4)

        self._grid = ThumbnailGridWidget(self._pool)
        self._grid.drop_files.connect(self._handle_dropped_files)
        self._grid.item_activated.connect(self._on_item_activated)
        self._grid.selection_changed.connect(self._on_selection_changed)

        self._detail = DetailPanel()
        self._detail.tags_committed.connect(self._on_tags_committed)
        self._detail.multi_tag_added.connect(self._on_multi_tag_added)
        self._detail.multi_tag_removed.connect(self._on_multi_tag_removed)
        self._detail.open_group.connect(self._open_group_window)
        self._detail.remove_from_group.connect(self._on_remove_from_group)
        self._detail.group_renamed.connect(self._on_group_renamed)
        self._detail.tags_copy_requested.connect(self._on_tags_copy)
        self._detail.tags_paste_requested.connect(self._on_tags_paste)

        splitter.addWidget(self._grid)
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([860, 340])

        main_layout.addWidget(splitter, 1)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status_label = QLabel()
        self._status.addWidget(self._status_label)

        # ツールバーアイコンを適用（初期テーマはライトモード）
        self._update_action_icons(self._dark_mode)

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("ファイル")
        file_menu.addAction(self._act_add)
        file_menu.addSeparator()
        act_quit = QAction("終了", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        album_menu = mb.addMenu("アルバム")
        act_new_album = QAction("新規アルバム…", self)
        act_new_album.triggered.connect(self._new_album)
        album_menu.addAction(act_new_album)
        act_rename_album = QAction("アルバム名変更…", self)
        act_rename_album.triggered.connect(self._rename_album)
        album_menu.addAction(act_rename_album)
        act_delete_album = QAction("アルバムを削除…", self)
        act_delete_album.triggered.connect(self._delete_album)
        album_menu.addAction(act_delete_album)
        album_menu.addSeparator()
        act_export = QAction("このアルバムをJSONに書き出し…", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.triggered.connect(self._export_json)
        album_menu.addAction(act_export)
        act_export_all = QAction("全アルバムをまとめてJSONに書き出し…", self)
        act_export_all.triggered.connect(self._export_json_combined)
        album_menu.addAction(act_export_all)
        album_menu.addSeparator()
        act_import = QAction("JSONからデータを読み込み…", self)
        act_import.triggered.connect(self._import_json)
        album_menu.addAction(act_import)

        view_menu = mb.addMenu("表示")
        view_menu.addAction(self._act_theme)

        tag_menu = mb.addMenu("タグ")
        act_tag_manager = QAction("タグ管理…", self)
        act_tag_manager.setShortcut(QKeySequence("Ctrl+T"))
        act_tag_manager.triggered.connect(self._open_tag_manager)
        tag_menu.addAction(act_tag_manager)

    # ------------------------------------------------------------------
    # Sort
    # ------------------------------------------------------------------

    def _on_sort_changed(self, key: str, desc: bool) -> None:
        self._sort_key_name = key
        self._sort_desc = desc
        self._save_sort()
        self._reload_display()

    def _get_img_sort_key(self, img: Image):
        if self._sort_key_name == "added":
            return img.added_at or datetime.min
        elif self._sort_key_name == "created":
            return img.ctime or 0.0
        else:  # "name"
            return natural_sort_key(img.filename)

    def _get_grp_sort_key(self, grp: Group):
        if not grp.images:
            if self._sort_key_name == "added":
                return grp.created_at or datetime.min
            elif self._sort_key_name == "created":
                return 0.0
            else:
                return natural_sort_key("")
        vals = [self._get_img_sort_key(img) for img in grp.images]
        # 降順の場合グループ内一番は max、昇順は min
        return max(vals) if self._sort_desc else min(vals)

    def _apply_sort(self, groups: list, images: list) -> list:
        """groups + images をソートした display リストを返す。"""
        grouped_ids = {img.id for g in groups for img in g.images}
        ungrouped = [img for img in images if img.id not in grouped_ids]
        merged = (
            [(self._get_grp_sort_key(g), g) for g in groups]
            + [(self._get_img_sort_key(img), img) for img in ungrouped]
        )
        merged.sort(key=lambda x: x[0], reverse=self._sort_desc)
        return [item for _, item in merged]

    # ------------------------------------------------------------------
    # Album management
    # ------------------------------------------------------------------

    def _init_album_combo(self) -> None:
        """コンボボックスをアルバムリストで初期化し、シグナルを接続する。"""
        self._album_combo.blockSignals(True)
        self._album_combo.clear()
        for name in self._album_manager.album_names:
            self._album_combo.addItem(name)
        idx = self._album_combo.findText(self._album_manager.active_name)
        if idx >= 0:
            self._album_combo.setCurrentIndex(idx)
        self._album_combo.blockSignals(False)
        self._album_combo.currentTextChanged.connect(self._on_album_changed)

    def _update_album_combo(self) -> None:
        """アルバムリスト変更後にコンボボックスを再構築する。"""
        self._album_combo.blockSignals(True)
        self._album_combo.clear()
        for name in self._album_manager.album_names:
            self._album_combo.addItem(name)
        idx = self._album_combo.findText(self._album_manager.active_name)
        if idx >= 0:
            self._album_combo.setCurrentIndex(idx)
        self._album_combo.blockSignals(False)

    def _on_album_changed(self, name: str) -> None:
        """コンボボックスでアルバムが切り替えられたとき。"""
        if not name or name == self._album_manager.active_name:
            return
        self._save_sort()  # 切り替え前のアルバムのソート設定を保存
        self._album_manager.set_active(name)
        init_db(self._album_manager.active_db_path())
        self._restore_sort()  # 新しいアルバムのソート設定を復元
        self._search_bar.set_text("")  # 検索条件をリセット
        self._refresh_grid()

    def _new_album(self) -> None:
        name, ok = QInputDialog.getText(self, "新規アルバム", "アルバム名:")
        if not ok or not name.strip():
            return
        try:
            db_path = self._album_manager.create_album(name.strip())
        except ValueError as e:
            QMessageBox.warning(self, "エラー", str(e))
            return
        # 新規 DB を初期化（空のテーブルを作成）
        init_db(db_path)
        # アクティブに切り替え
        self._album_manager.set_active(name.strip())
        init_db(self._album_manager.active_db_path())
        self._update_album_combo()
        self._refresh_grid()

    def _rename_album(self) -> None:
        current = self._album_manager.active_name
        new_name, ok = QInputDialog.getText(
            self, "アルバム名変更", "新しい名前:", text=current
        )
        if not ok or not new_name.strip():
            return
        try:
            self._album_manager.rename_album(current, new_name.strip())
        except ValueError as e:
            QMessageBox.warning(self, "エラー", str(e))
            return
        self._update_album_combo()
        self._refresh_grid()

    def _delete_album(self) -> None:
        current = self._album_manager.active_name
        if len(self._album_manager.album_names) <= 1:
            QMessageBox.information(self, "削除不可", "最後のアルバムは削除できません。")
            return
        reply = QMessageBox.warning(
            self, "アルバムを削除",
            f"アルバム「{current}」を削除します。\n"
            "このアルバム内のデータ（画像管理情報・タグ）はすべて失われます。\n"
            "元のファイルは削除されません。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._album_manager.delete_album(current)
        # 新しいアクティブアルバムのDBに切り替え
        init_db(self._album_manager.active_db_path())
        self._update_album_combo()
        self._refresh_grid()

    # ------------------------------------------------------------------
    # Tag manager
    # ------------------------------------------------------------------

    def _open_tag_manager(self) -> None:
        if not hasattr(self, "_tag_manager_dlg") or self._tag_manager_dlg is None:
            self._tag_manager_dlg = TagManagerDialog(self)
            self._tag_manager_dlg.search_requested.connect(self._on_tag_manager_search)
            self._tag_manager_dlg.color_changed.connect(self._on_tag_color_changed)
            self._tag_manager_dlg.finished.connect(lambda _: setattr(self, "_tag_manager_dlg", None))
        self._tag_manager_dlg.show()
        self._tag_manager_dlg.raise_()
        self._tag_manager_dlg.activateWindow()

    def _on_tag_manager_search(self, tag_name: str) -> None:
        self._search_bar.set_text(tag_name)
        self._do_search([tag_name], "and")

    def _on_tag_color_changed(self) -> None:
        """タグ管理ダイアログで色が変更されたとき、詳細パネルとグリッドの色を即時更新する。"""
        with get_session() as session:
            cmap = all_tag_color_map(session)
        self._detail.set_color_map(cmap)
        self._grid.update_tag_colors(cmap)

    # ------------------------------------------------------------------
    # Grid refresh
    # ------------------------------------------------------------------

    def _refresh_grid(self) -> None:
        self._view_mode = "home"
        self._breadcrumb.set_home()
        album = self._album_manager.active_name
        self.setWindowTitle(f"{__app_name__} {__version__}  —  {album}")
        with get_session() as session:
            self._cached_images = all_images(session)
            self._cached_groups = all_groups(session)
            tags      = all_tag_names(session)
            tag_infos = [(t.name, cnt, t.color) for t, cnt in all_tags_with_count(session)]
            cmap      = all_tag_color_map(session)
        self._detail.set_completion_list(tags)
        self._detail.set_color_map(cmap)
        self._search_bar.set_completion_list(tag_infos)
        self._reload_display()

    def _reload_display(self) -> None:
        """DBアクセスなし。キャッシュ済みデータを再ソートしてグリッドに表示する。"""
        display = self._apply_sort(self._cached_groups, self._cached_images)
        self._grid.load_items(display)
        self._detail.clear()
        self._update_status(len(display))

    def _update_status(self, count: int) -> None:
        self._status_label.setText(f"{count} アイテム")

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------

    def _open_images(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "画像を選択",
            "",
            "画像ファイル (*.png *.jpg *.jpeg *.gif *.webp)",
        )
        if paths:
            self._import_paths(paths)

    def _handle_dropped_files(self, paths: list[str]) -> None:
        valid = [
            p for p in paths
            if Path(p).suffix.lower() in SUPPORTED_EXTS
        ]
        if valid:
            self._import_paths(valid)
        else:
            QMessageBox.warning(self, "対応外のファイル", "対応している画像形式ではありません。")

    def _import_paths(self, paths: list[str]) -> None:
        added, skipped = add_images(paths)
        self._refresh_grid()
        parts = []
        if added:
            parts.append(f"{added} 枚追加しました")
        if skipped:
            parts.append(f"{skipped} 枚は重複のためスキップしました")
        if not parts:
            parts.append("追加できるファイルがありませんでした")
        self._status_label.setText("  ／  ".join(parts))

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    def _on_group_action(self) -> None:
        """選択状態に応じてグループ化またはグループ結合を実行する。"""
        if getattr(self, "_group_action_is_merge", False):
            self._merge_groups()
        else:
            self._create_group()

    def _create_group(self) -> None:
        selected = self._grid.selected_items_data()
        images = [s for s in selected if isinstance(s, Image)]
        if len(images) < 2:
            QMessageBox.information(self, "グループ化", "2枚以上の画像を選択してください。")
            return
        dlg = GroupDialog([img.filename for img in images], parent=self)
        if dlg.exec() != GroupDialog.DialogCode.Accepted:
            return
        with get_session() as session:
            create_group(session, dlg.group_name, [img.id for img in images])
            session.commit()
        self._refresh_current_view()

    def _merge_groups(self) -> None:
        selected = self._grid.selected_items_data()
        groups = [s for s in selected if isinstance(s, Group)]
        images = [s for s in selected if isinstance(s, Image)]
        if not groups:
            return
        # メンバー一覧をダイアログに表示（グループ名＋画像ファイル名）
        member_names = [f"⊞ {g.name}" for g in groups] + [img.filename for img in images]
        dlg = GroupDialog(member_names, parent=self)
        dlg.setWindowTitle("グループ結合")
        if dlg.exec() != GroupDialog.DialogCode.Accepted:
            return
        with get_session() as session:
            merge_groups(
                session,
                dlg.group_name,
                [g.id for g in groups],
                [img.id for img in images],
            )
            session.commit()
        self._refresh_current_view()

    def _on_regex_group_action(self) -> None:
        """正規表現でファイル名を指定してグループ化する。"""
        import re

        last_pattern = ""
        while True:
            # ステップ 2-4: 正規表現入力
            input_dlg = RegexInputDialog(default_pattern=last_pattern, parent=self)
            if input_dlg.exec() != RegexInputDialog.DialogCode.Accepted:
                return

            pattern = input_dlg.pattern
            last_pattern = pattern  # エラー時も入力値を次回に引き継ぐ

            if not pattern:
                QMessageBox.warning(self, "正規表現グループ化", "正規表現を入力してください。")
                continue

            try:
                rx = re.compile(pattern)
            except re.error as e:
                QMessageBox.warning(self, "正規表現エラー", f"正規表現が不正です:\n{e}")
                continue

            # グループ化されていない画像に絞ってマッチング
            ungrouped = [img for img in self._cached_images if img.group_id is None]
            matched = [img for img in ungrouped if rx.search(img.filename)]

            if not matched:
                QMessageBox.information(
                    self, "該当なし",
                    "条件に合致する未グループ化の画像が見つかりませんでした。\n"
                    "正規表現を変更してください。",
                )
                continue  # ステップ 2 に戻る

            # ステップ 5-6: マッチ結果確認・グループ化
            while True:
                preview_dlg = RegexGroupPreviewDialog(matched, self._pool, parent=self)
                if preview_dlg.exec() != RegexGroupPreviewDialog.DialogCode.Accepted:
                    return  # キャンセル → グループ化しない

                selected_images = preview_dlg.selected_images
                if len(selected_images) < 2:
                    QMessageBox.information(
                        self, "グループ化",
                        "グループ化するには 2 枚以上の画像を選択してください。",
                    )
                    continue

                with get_session() as session:
                    create_group(session, preview_dlg.group_name, [img.id for img in selected_images])
                    session.commit()
                self._refresh_current_view()
                return

    def _dissolve_group(self) -> None:
        selected = self._grid.selected_items_data()
        groups = [s for s in selected if isinstance(s, Group)]
        if not groups:
            return
        if QMessageBox.question(
            self, "グループ解除",
            f"{len(groups)} 個のグループを解除しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        group_ids = [g.id for g in groups]
        with get_session() as session:
            fresh_groups = list(
                session.execute(select(Group).where(Group.id.in_(group_ids))).scalars()
            )
            for g in fresh_groups:
                dissolve_group(session, g)
            session.commit()
        self._refresh_current_view()

    # ------------------------------------------------------------------
    # Thumbnail regeneration
    # ------------------------------------------------------------------

    def _regen_selected_thumbs(self) -> None:
        selected = self._grid.selected_items_data()
        if not selected:
            return
        # 対象となる画像を収集（Image直接 + Groupのカバー画像）
        images_to_regen: list[Image] = []
        for item in selected:
            if isinstance(item, Image):
                images_to_regen.append(item)
            elif isinstance(item, Group) and item.cover_image:
                images_to_regen.append(item.cover_image)
        for img in images_to_regen:
            self._cache.invalidate(img.path)
            self._pool.request(img.id, img.path, self._grid.update_thumb)

    # ------------------------------------------------------------------
    # Remove
    # ------------------------------------------------------------------

    def _remove_selected(self) -> None:
        selected = self._grid.selected_items_data()
        if not selected:
            return
        if QMessageBox.question(
            self,
            "削除",
            f"{len(selected)} 件をPixellaの管理から除外しますか？\n(元のファイルは削除されません)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        image_ids = [i.id for i in selected if isinstance(i, Image)]
        group_ids  = [i.id for i in selected if isinstance(i, Group)]
        with get_session() as session:
            from pixella.db.repository import remove_image
            for img in session.execute(select(Image).where(Image.id.in_(image_ids))).scalars():
                remove_image(session, img)
            for grp in session.execute(select(Group).where(Group.id.in_(group_ids))).scalars():
                dissolve_group(session, grp)
            session.commit()
        self._refresh_current_view()

    # ------------------------------------------------------------------
    # Drill into group
    # ------------------------------------------------------------------

    def _on_item_activated(self, item) -> None:
        if isinstance(item, Group):
            self._open_group_window(item)
        elif isinstance(item, Image):
            with get_session() as session:
                fresh = session.execute(
                    select(Image).where(Image.id == item.id)
                    .options(selectinload(Image.tags), selectinload(Image.group))
                ).scalar_one_or_none()
                tags = all_tag_names(session)
            if fresh:
                self._detail.show_image(fresh)
            self._detail.set_completion_list(tags)

    def _open_group_window(self, group: Group) -> None:
        """グループウィンドウを開く（既に開いている場合は前面に出す）。"""
        if group.id in self._group_windows:
            win = self._group_windows[group.id]
            win.raise_()
            win.activateWindow()
            return
        with get_session() as session:
            fresh = session.execute(
                select(Group).where(Group.id == group.id)
                .options(
                    selectinload(Group.images).selectinload(Image.tags),
                    selectinload(Group.cover_image),
                )
            ).scalar_one_or_none()
        if fresh is None:
            return
        win = GroupWindow(fresh, self._pool, sort_key=self._sort_key_name, sort_desc=self._sort_desc, parent=self)
        win.destroyed.connect(lambda _o, gid=group.id: self._group_windows.pop(gid, None))
        self._group_windows[group.id] = win
        win.show()

    def _go_back(self) -> None:
        self._search_bar.set_text("")
        self._refresh_grid()
        self.setWindowTitle(f"{__app_name__} {__version__}")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_selection_changed(self, items: list) -> None:
        has_images  = any(isinstance(i, Image) for i in items)
        has_groups  = any(isinstance(i, Group) for i in items)
        num_images  = sum(1 for i in items if isinstance(i, Image))
        num_groups  = sum(1 for i in items if isinstance(i, Group))
        multi_img   = num_images >= 2

        # グループ化: 画像のみ2枚以上選択（グループなし）
        can_group = multi_img and not has_groups
        # グループ結合: グループを含み、合計2アイテム以上
        can_merge = has_groups and (num_groups + num_images >= 2)
        if can_merge:
            self._act_group.setText("グループ結合")
            self._act_group.setEnabled(True)
            self._group_action_is_merge = True
        elif can_group:
            self._act_group.setText("グループ化")
            self._act_group.setEnabled(True)
            self._group_action_is_merge = False
        else:
            self._act_group.setText("グループ化")
            self._act_group.setEnabled(False)
            self._group_action_is_merge = False
        # グループ結合: グループを含み、合計2アイテム以上
        self._act_dissolve.setEnabled(has_groups)
        self._act_remove.setEnabled(bool(items))
        self._act_regen_thumb.setEnabled(bool(items))

        if len(items) == 1:
            item = items[0]
            with get_session() as session:
                if isinstance(item, Image):
                    fresh = session.execute(
                        select(Image).where(Image.id == item.id)
                        .options(selectinload(Image.tags), selectinload(Image.group))
                    ).scalar_one_or_none()
                    if fresh:
                        self._detail.show_image(fresh)
                else:
                    fresh = session.execute(
                        select(Group).where(Group.id == item.id)
                        .options(selectinload(Group.tags), selectinload(Group.images))
                    ).scalar_one_or_none()
                    if fresh:
                        self._detail.show_group(fresh)
                self._detail.set_completion_list(all_tag_names(session))
                self._detail.set_color_map(all_tag_color_map(session))
        elif len(items) > 1:
            image_ids = [i.id for i in items if isinstance(i, Image)]
            group_ids = [i.id for i in items if isinstance(i, Group)]
            with get_session() as session:
                fresh_images = list(
                    session.execute(
                        select(Image).where(Image.id.in_(image_ids))
                        .options(selectinload(Image.tags))
                    ).scalars()
                ) if image_ids else []
                fresh_groups = list(
                    session.execute(
                        select(Group).where(Group.id.in_(group_ids))
                        .options(selectinload(Group.tags), selectinload(Group.images))
                    ).scalars()
                ) if group_ids else []
                tags = all_tag_names(session)
                cmap = all_tag_color_map(session)
            self._detail.show_multi_images(fresh_images, fresh_groups)
            self._detail.set_completion_list(tags)
            self._detail.set_color_map(cmap)
        elif not items:
            self._detail.clear()

        # クリップボード状態に合わせて貼り付けボタンの活性状態を常に同期する
        self._detail.set_clipboard_available(bool(self._tag_clipboard))

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------

    def _on_tags_committed(self, tags: list[str]) -> None:
        """1アイテム選択時のタグ保存。グリッドの実選択からアイテムを取得して書き込む。"""
        selected = self._grid.selected_items_data()
        if len(selected) != 1:
            return  # 選択状態が変わっていたら無視
        item = selected[0]
        removed: set[str] = set()
        with get_session() as session:
            if isinstance(item, Image):
                # group も eager ロードしてキャッシュ置換後に DetachedInstanceError が出ないようにする
                fresh = session.execute(
                    select(Image).where(Image.id == item.id)
                    .options(selectinload(Image.tags), selectinload(Image.group))
                ).scalar_one_or_none()
                if fresh:
                    removed = {t.name for t in fresh.tags} - {n.strip().lower() for n in tags if n.strip()}
                    set_image_tags(session, fresh, tags)
            else:
                # cover_image / images も eager ロードしてキャッシュ置換後に DetachedInstanceError が出ないようにする
                fresh = session.execute(
                    select(Group).where(Group.id == item.id)
                    .options(
                        selectinload(Group.tags),
                        selectinload(Group.cover_image),
                        selectinload(Group.images).selectinload(Image.tags),
                    )
                ).scalar_one_or_none()
                if fresh:
                    removed = {t.name for t in fresh.tags} - {n.strip().lower() for n in tags if n.strip()}
                    set_group_tags(session, fresh, tags)
            if fresh and removed:
                cleanup_uncolored_orphan_tags(session, list(removed))
            session.commit()
            # チップをセッション内でまとめて更新（detach 前に色情報を取得）
            if fresh:
                self._grid.set_item_tags(fresh)
                # ソート変更などで _reload_display() が呼ばれてもチップが戻らないよう
                # _cached_images / _cached_groups 内の対応アイテムを最新データで差し替える
                if isinstance(fresh, Image):
                    for i, img in enumerate(self._cached_images):
                        if img.id == fresh.id:
                            self._cached_images[i] = fresh
                            break
                else:
                    for i, grp in enumerate(self._cached_groups):
                        if grp.id == fresh.id:
                            self._cached_groups[i] = fresh
                            break
            # commit() 後に同一セッションで補完リストを取得（セッション開閉のオーバーヘッドを削減）
            all_t     = all_tag_names(session)
            tag_infos = [(t.name, cnt, t.color) for t, cnt in all_tags_with_count(session)]
        self._detail.set_completion_list(all_t)
        QTimer.singleShot(0, lambda infos=tag_infos: self._search_bar.set_completion_list(infos))

    def _on_multi_tag_added(self, items: list, tag: str) -> None:
        """複数アイテム選択時: 指定タグを全画像・グループに追加する。"""
        image_ids = [i.id for i in items if isinstance(i, Image)]
        group_ids = [i.id for i in items if isinstance(i, Group)]
        bulk_apply_tag_delta(image_ids, added={tag}, removed=set())
        bulk_apply_group_tag_delta(group_ids, added={tag}, removed=set())
        with get_session() as session:
            all_t     = all_tag_names(session)
            tag_infos = [(t.name, cnt, t.color) for t, cnt in all_tags_with_count(session)]
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags), selectinload(Image.group))
                ).scalars():
                    self._grid.set_item_tags(img)
                    for i, cached in enumerate(self._cached_images):
                        if cached.id == img.id:
                            self._cached_images[i] = img
                            break
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(
                        selectinload(Group.tags),
                        selectinload(Group.cover_image),
                        selectinload(Group.images).selectinload(Image.tags),
                    )
                ).scalars():
                    self._grid.set_item_tags(grp)
                    for i, cached in enumerate(self._cached_groups):
                        if cached.id == grp.id:
                            self._cached_groups[i] = grp
                            break
        self._detail.set_completion_list(all_t)
        QTimer.singleShot(0, lambda infos=tag_infos: self._search_bar.set_completion_list(infos))

    def _on_multi_tag_removed(self, items: list, tag: str) -> None:
        """複数アイテム選択時: 指定タグを全画像・グループから削除する。"""
        image_ids = [i.id for i in items if isinstance(i, Image)]
        group_ids = [i.id for i in items if isinstance(i, Group)]
        bulk_apply_tag_delta(image_ids, added=set(), removed={tag})
        bulk_apply_group_tag_delta(group_ids, added=set(), removed={tag})
        with get_session() as session:
            # タグが孤立して無色なら自動削除（bulk_apply 後に実施）
            cleanup_uncolored_orphan_tags(session, [tag])
            session.commit()
            all_t     = all_tag_names(session)  # cleanup 後に取得
            tag_infos = [(t.name, cnt, t.color) for t, cnt in all_tags_with_count(session)]
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags), selectinload(Image.group))
                ).scalars():
                    self._grid.set_item_tags(img)
                    for i, cached in enumerate(self._cached_images):
                        if cached.id == img.id:
                            self._cached_images[i] = img
                            break
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(
                        selectinload(Group.tags),
                        selectinload(Group.cover_image),
                        selectinload(Group.images).selectinload(Image.tags),
                    )
                ).scalars():
                    self._grid.set_item_tags(grp)
                    for i, cached in enumerate(self._cached_groups):
                        if cached.id == grp.id:
                            self._cached_groups[i] = grp
                            break
        self._detail.set_completion_list(all_t)
        QTimer.singleShot(0, lambda infos=tag_infos: self._search_bar.set_completion_list(infos))

    def _on_remove_from_group(self, image: Image) -> None:
        with get_session() as session:
            fresh = session.execute(
                select(Image).where(Image.id == image.id)
            ).scalar_one_or_none()
            if fresh:
                remove_image_from_group(session, fresh)
            session.commit()
        self._refresh_grid()

    def _on_group_renamed(self, group: Group, new_name: str) -> None:
        with get_session() as session:
            rename_group(session, group.id, new_name)
            session.commit()
        # グリッドアイテムのラベルとデータを更新
        from PySide6.QtCore import Qt
        lw_item = self._grid._id_to_item.get(f"grp:{group.id}")
        if lw_item:
            grp_data = lw_item.data(Qt.ItemDataRole.UserRole)
            if isinstance(grp_data, Group):
                grp_data.name = new_name
            lw_item.setText(f"⊞ {new_name}")
        # 詳細パネルのグループ名を更新
        with get_session() as session:
            fresh = session.execute(
                select(Group).where(Group.id == group.id)
                .options(
                    selectinload(Group.tags),
                    selectinload(Group.images),
                    selectinload(Group.cover_image),
                )
            ).scalar_one_or_none()
            if fresh:
                self._detail.show_group(fresh)
        # グループウィンドウのタイトルも更新
        if group.id in self._group_windows:
            self._group_windows[group.id].setWindowTitle(f"⊞ {new_name}")

    # ------------------------------------------------------------------
    # Tag copy / paste
    # ------------------------------------------------------------------

    def _on_tags_copy(self) -> None:
        """現在選択中のアイテムのタグをクリップボードに保存する。"""
        selected = self._grid.selected_items_data()
        if not selected:
            return
        # 単一選択はそのタグ、複数選択は共通タグをコピー
        if len(selected) == 1:
            item = selected[0]
            with get_session() as session:
                if isinstance(item, Image):
                    fresh = session.execute(
                        select(Image).where(Image.id == item.id)
                        .options(selectinload(Image.tags))
                    ).scalar_one_or_none()
                else:
                    fresh = session.execute(
                        select(Group).where(Group.id == item.id)
                        .options(selectinload(Group.tags))
                    ).scalar_one_or_none()
                self._tag_clipboard = [t.name for t in fresh.tags] if fresh else []
        else:
            # 複数選択: 全アイテムの共通タグをコピー
            item_ids_images = [i.id for i in selected if isinstance(i, Image)]
            item_ids_groups = [i.id for i in selected if isinstance(i, Group)]
            with get_session() as session:
                all_items = []
                if item_ids_images:
                    all_items += list(session.execute(
                        select(Image).where(Image.id.in_(item_ids_images))
                        .options(selectinload(Image.tags))
                    ).scalars())
                if item_ids_groups:
                    all_items += list(session.execute(
                        select(Group).where(Group.id.in_(item_ids_groups))
                        .options(selectinload(Group.tags))
                    ).scalars())
                if all_items:
                    common = set(t.name for t in all_items[0].tags)
                    for it in all_items[1:]:
                        common &= {t.name for t in it.tags}
                    self._tag_clipboard = sorted(common)
                else:
                    self._tag_clipboard = []
        self._detail.set_clipboard_available(bool(self._tag_clipboard))
        count = len(self._tag_clipboard)
        self._status_label.setText(
            f"タグ {count} 件をコピーしました: {', '.join(self._tag_clipboard)}"
            if count else "コピーするタグがありません"
        )

    def _on_tags_paste(self) -> None:
        """クリップボードのタグを現在の選択に追加する。"""
        if not self._tag_clipboard:
            return
        selected = self._grid.selected_items_data()
        if not selected:
            return
        image_ids = [i.id for i in selected if isinstance(i, Image)]
        group_ids = [i.id for i in selected if isinstance(i, Group)]
        bulk_apply_tag_delta(image_ids, added=set(self._tag_clipboard), removed=set())
        bulk_apply_group_tag_delta(group_ids, added=set(self._tag_clipboard), removed=set())
        with get_session() as session:
            all_t     = all_tag_names(session)
            tag_infos = [(t.name, cnt, t.color) for t, cnt in all_tags_with_count(session)]
            cmap = all_tag_color_map(session)
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags), selectinload(Image.group))
                ).scalars():
                    self._grid.set_item_tags(img)
                    for i, cached in enumerate(self._cached_images):
                        if cached.id == img.id:
                            self._cached_images[i] = img
                            break
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(
                        selectinload(Group.tags),
                        selectinload(Group.cover_image),
                        selectinload(Group.images).selectinload(Image.tags),
                    )
                ).scalars():
                    self._grid.set_item_tags(grp)
                    for i, cached in enumerate(self._cached_groups):
                        if cached.id == grp.id:
                            self._cached_groups[i] = grp
                            break
        self._detail.set_completion_list(all_t)
        self._detail.set_color_map(cmap)
        self._search_bar.set_completion_list(tag_infos)
        # 詳細パネルを再表示
        self._on_selection_changed(selected)
        self._status_label.setText(f"タグ {len(self._tag_clipboard)} 件を貼り付けました")

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _do_search(self, tags: list[str], mode: str = "and") -> None:
        from pixella.db.repository import search_by_tags
        self._view_mode = "search"
        self._view_search_tags = list(tags)
        self._view_search_mode = mode
        mode_label = {"or": "OR", "exact": "完全一致"}.get(mode, "AND")
        self._breadcrumb.set_search(f"[{mode_label}] {' '.join(tags)}")
        with get_session() as session:
            images, groups = search_by_tags(session, tags, mode)
            cmap = all_tag_color_map(session)

        display = self._apply_sort(groups, images)
        self._grid.load_items(display)
        self._detail.set_color_map(cmap)
        self._update_status(len(display))
        self.setWindowTitle(f"{__app_name__} — 検索: {' '.join(tags)}")

    def _show_untagged(self) -> None:
        self._view_mode = "untagged"
        self._breadcrumb.set_search("タグなし")
        with get_session() as session:
            images = images_without_tags(session)
            groups = groups_without_tags(session)
            cmap = all_tag_color_map(session)
        display = self._apply_sort(groups, images)
        self._grid.load_items(display)
        self._detail.set_color_map(cmap)
        self._update_status(len(display))
        self.setWindowTitle(f"{__app_name__} — タグなし")

    def _refresh_current_view(self) -> None:
        """現在のビューモードを引き継いで再描画する。"""
        if self._view_mode == "untagged":
            self._show_untagged()
        elif self._view_mode == "search" and self._view_search_tags:
            self._do_search(self._view_search_tags, self._view_search_mode)
        else:
            self._refresh_grid()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def _update_action_icons(self, dark: bool) -> None:
        """テーマに合わせた Segoe Fluent Icons をツールバーアクションに適用する。"""
        from pixella.ui.fluent_icons import FluentGlyph, make_fluent_icon
        color = QColor("#EFEFEF" if dark else "#1F1F1F")
        sz = 20
        self._act_add.setIcon(make_fluent_icon(FluentGlyph.ADD, sz, color))
        self._act_group.setIcon(make_fluent_icon(FluentGlyph.GROUP, sz, color))
        self._act_regex_group.setIcon(make_fluent_icon(FluentGlyph.SEARCH, sz, color))
        self._act_dissolve.setIcon(make_fluent_icon(FluentGlyph.UNGROUP, sz, color))
        self._act_remove.setIcon(make_fluent_icon(FluentGlyph.DELETE, sz, color))
        self._act_regen_thumb.setIcon(make_fluent_icon(FluentGlyph.REFRESH, sz, color))
        theme_glyph = FluentGlyph.BRIGHTNESS if dark else FluentGlyph.MOON
        self._act_theme.setIcon(make_fluent_icon(theme_glyph, sz, color))

    def _toggle_theme(self, checked: bool) -> None:
        self._dark_mode = checked
        self._act_theme.setText("ライトモード" if checked else "ダークモード")
        apply_theme(QApplication.instance(), self._dark_mode)
        self._update_action_icons(self._dark_mode)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_json(self) -> None:
        """アクティブなアルバムのデータをJSONに書き出す。"""
        last_dir = self._settings().value("json_last_dir", "")
        album = self._album_manager.active_name
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in album)
        default_name = f"pixella_{safe_name}.json"
        default_path = str(Path(last_dir) / default_name) if last_dir else default_name
        path, _ = QFileDialog.getSaveFileName(
            self, f"「{album}」をJSONに書き出し", default_path, "JSON (*.json)"
        )
        if path:
            self._settings().setValue("json_last_dir", str(Path(path).parent))
            with get_session() as session:
                export_json(session, path)
            self._status.showMessage(f"書き出し完了: {path}", 5000)

    def _export_json_combined(self) -> None:
        """全アルバムをまとめて1つのJSONに書き出す。"""
        last_dir = self._settings().value("json_last_dir", "")
        default_path = str(Path(last_dir) / "pixella_all_albums.json") if last_dir else "pixella_all_albums.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "全アルバムをJSONに書き出し", default_path, "JSON (*.json)"
        )
        if not path:
            return
        self._settings().setValue("json_last_dir", str(Path(path).parent))
        try:
            export_json_combined(self._album_manager.all_db_paths(), path)
        except Exception as e:
            QMessageBox.critical(self, "書き出しエラー", f"書き出しに失敗しました:\n{e}")
            return
        n = len(self._album_manager.album_names)
        self._status.showMessage(f"全 {n} アルバムを書き出し完了: {path}", 5000)

    def _import_json(self) -> None:
        last_dir = self._settings().value("json_last_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "JSONから読み込み", last_dir, "JSON (*.json)"
        )
        if not path:
            return
        self._settings().setValue("json_last_dir", str(Path(path).parent))
        # JSONを読み込んでフォーマットを判定
        try:
            import json as _json
            raw = Path(path).read_text(encoding="utf-8")
            data = _json.loads(raw)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", f"JSONのパースに失敗しました:\n{e}")
            return

        # マルチアルバム形式かどうかを判定
        if data.get("format") == "pixella_multi_album":
            self._import_json_multi(data)
            return

        # 単一アルバム形式（従来互換）
        try:
            _validate_import_json(data)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", f"JSONの検証に失敗しました:\n{e}")
            return
        n_images = len(data.get("images", []))
        n_groups = len(data.get("groups", []))
        n_tags   = len(data.get("tags", []))
        reply = QMessageBox.warning(
            self,
            "データの読み込み確認",
            f"アルバム「{self._album_manager.active_name}」の現在のデータはすべて削除されます。\n"
            f"本当に読み込みますか？\n\n"
            f"読み込む内容: 画像 {n_images} 件 / グループ {n_groups} 件 / タグ {n_tags} 件",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            import_json(path)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", f"読み込みに失敗しました:\n{e}")
            return
        self._refresh_grid()
        QMessageBox.information(self, "読み込み完了", f"読み込みました:\n{path}")

    def _import_json_multi(self, data: dict) -> None:
        """マルチアルバム形式JSONのインポート処理。"""
        albums_data = data.get("albums", [])
        if not albums_data:
            QMessageBox.warning(self, "読み込みエラー", "アルバムデータが空です。")
            return

        names = [a.get("name", f"アルバム {i+1}") for i, a in enumerate(albums_data)]
        names_str = "\n".join(f"  • {n}" for n in names)
        reply = QMessageBox.warning(
            self,
            "マルチアルバム読み込み",
            f"{len(albums_data)} 件のアルバムを読み込みます:\n{names_str}\n\n"
            "同名のアルバムが存在する場合はデータが上書きされます。\n"
            "新しいアルバム名は自動的に作成されます。続行しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        original_active = self._album_manager.active_name
        errors: list[str] = []

        for album_data in albums_data:
            name = album_data.get("name", "インポートアルバム")
            try:
                _validate_import_json(album_data)
            except ValueError as e:
                errors.append(f"「{name}」のバリデーションエラー: {e}")
                continue
            # アルバムが存在しなければ作成
            try:
                db_path = self._album_manager.get_db_path(name)
            except KeyError:
                try:
                    db_path = self._album_manager.create_album(name)
                except ValueError as e:
                    errors.append(f"「{name}」の作成エラー: {e}")
                    continue
            # 対象アルバムのDBを初期化してインポート
            init_db(db_path)
            try:
                with get_session() as session:
                    _do_import(session, album_data)
            except Exception as e:
                errors.append(f"「{name}」のインポートエラー: {e}")

        # アクティブアルバムのDBに戻す
        try:
            init_db(self._album_manager.active_db_path())
        except KeyError:
            # アクティブアルバムが消えていた場合は先頭へ
            self._album_manager.set_active(self._album_manager.album_names[0])
            init_db(self._album_manager.active_db_path())

        self._update_album_combo()
        self._refresh_grid()

        if errors:
            QMessageBox.warning(
                self, "一部エラーあり",
                f"{len(albums_data) - len(errors)} 件成功, {len(errors)} 件失敗:\n" +
                "\n".join(errors)
            )
        else:
            QMessageBox.information(
                self, "読み込み完了",
                f"{len(albums_data)} 件のアルバムを読み込みました。"
            )
