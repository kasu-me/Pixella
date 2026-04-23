"""Main application window."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QSplitter, QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

from pixella import __app_name__, __version__
from pixella.core import ThumbnailCache, ThumbnailWorkerPool, THUMB_DIR, SUPPORTED_EXTS
from pixella.db import (
    get_session, all_images, all_groups, all_tag_names, all_tag_color_map,
    add_images, images_without_tags, groups_without_tags, create_group, merge_groups, rename_group, dissolve_group,
    remove_image_from_group, set_image_tags, set_group_tags,
    export_json, import_json, bulk_apply_tag_delta, bulk_apply_group_tag_delta,
    cleanup_uncolored_orphan_tags,
)
from pixella.db.models import Image, Group, Tag
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pixella.ui.grid_view import ThumbnailGridWidget
from pixella.ui.detail_panel import DetailPanel
from pixella.ui.search_bar import SearchBar
from pixella.ui.dialogs import GroupDialog
from pixella.ui.themes import apply_theme
from pixella.ui.breadcrumb import BreadcrumbBar
from pixella.ui.sort_bar import SortBar
from pixella.ui.tag_manager import TagManagerDialog
from pixella.ui.group_window import GroupWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
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

    def _restore_sort(self) -> None:
        s = self._settings()
        key  = s.value("sort/key",  "added")
        desc = s.value("sort/desc", False)
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
        s.setValue("sort/key",  self._sort_key_name)
        s.setValue("sort/desc", self._sort_desc)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Toolbar
        toolbar = QToolBar("メイン")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._act_add = QAction("＋ 画像を追加", self)
        self._act_add.setShortcut(QKeySequence("Ctrl+O"))
        self._act_add.triggered.connect(self._open_images)
        toolbar.addAction(self._act_add)

        toolbar.addSeparator()

        self._act_group = QAction("⊞ グループ化", self)
        self._act_group.setShortcut(QKeySequence("Ctrl+G"))
        self._act_group.setEnabled(False)
        self._act_group.triggered.connect(self._create_group)
        toolbar.addAction(self._act_group)

        self._act_merge = QAction("⊞ グループ結合", self)
        self._act_merge.setShortcut(QKeySequence("Ctrl+M"))
        self._act_merge.setEnabled(False)
        self._act_merge.triggered.connect(self._merge_groups)
        toolbar.addAction(self._act_merge)

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

        toolbar.addSeparator()

        self._act_theme = QAction("🌙 ダークモード", self)
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

    def _build_menu(self) -> None:
        mb = self.menuBar()

        file_menu = mb.addMenu("ファイル")
        file_menu.addAction(self._act_add)
        act_export = QAction("データをJSON書き出し…", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.triggered.connect(self._export_json)
        file_menu.addAction(act_export)
        act_import = QAction("JSONからデータを読み込み…", self)
        act_import.triggered.connect(self._import_json)
        file_menu.addAction(act_import)
        file_menu.addSeparator()
        act_quit = QAction("終了", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

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
        self._reload_display()

    def _get_img_sort_key(self, img: Image):
        if self._sort_key_name == "added":
            return img.added_at or datetime.min
        elif self._sort_key_name == "created":
            return img.ctime or 0.0
        else:  # "name"
            return img.filename.lower()

    def _get_grp_sort_key(self, grp: Group):
        if not grp.images:
            if self._sort_key_name == "added":
                return grp.created_at or datetime.min
            elif self._sort_key_name == "created":
                return 0.0
            else:
                return ""
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
        self.setWindowTitle(f"{__app_name__} {__version__}")
        with get_session() as session:
            self._cached_images = all_images(session)
            self._cached_groups = all_groups(session)
            tags   = all_tag_names(session)
            cmap   = all_tag_color_map(session)
        self._detail.set_completion_list(tags)
        self._detail.set_color_map(cmap)
        self._search_bar.set_completion_list(tags)
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
        win = GroupWindow(fresh, self._pool, parent=self)
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
        self._act_group.setEnabled(multi_img and not has_groups)
        # グループ結合: グループを含み、合計2アイテム以上
        can_merge = has_groups and (num_groups + num_images >= 2)
        self._act_merge.setEnabled(can_merge)
        self._act_dissolve.setEnabled(has_groups)
        self._act_remove.setEnabled(bool(items))

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
                fresh = session.execute(
                    select(Image).where(Image.id == item.id)
                    .options(selectinload(Image.tags))
                ).scalar_one_or_none()
                if fresh:
                    removed = {t.name for t in fresh.tags} - {n.strip().lower() for n in tags if n.strip()}
                    set_image_tags(session, fresh, tags)
            else:
                fresh = session.execute(
                    select(Group).where(Group.id == item.id)
                    .options(selectinload(Group.tags))
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
        with get_session() as session:
            all_t = all_tag_names(session)
        self._detail.set_completion_list(all_t)
        self._search_bar.set_completion_list(all_t)

    def _on_multi_tag_added(self, items: list, tag: str) -> None:
        """複数アイテム選択時: 指定タグを全画像・グループに追加する。"""
        image_ids = [i.id for i in items if isinstance(i, Image)]
        group_ids = [i.id for i in items if isinstance(i, Group)]
        bulk_apply_tag_delta(image_ids, added={tag}, removed=set())
        bulk_apply_group_tag_delta(group_ids, added={tag}, removed=set())
        with get_session() as session:
            all_t = all_tag_names(session)
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags))
                ).scalars():
                    self._grid.set_item_tags(img)
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(selectinload(Group.tags))
                ).scalars():
                    self._grid.set_item_tags(grp)
        self._detail.set_completion_list(all_t)
        self._search_bar.set_completion_list(all_t)

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
            all_t = all_tag_names(session)  # cleanup 後に取得
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags))
                ).scalars():
                    self._grid.set_item_tags(img)
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(selectinload(Group.tags))
                ).scalars():
                    self._grid.set_item_tags(grp)
        self._detail.set_completion_list(all_t)
        self._search_bar.set_completion_list(all_t)

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
            all_t = all_tag_names(session)
            cmap = all_tag_color_map(session)
            if image_ids:
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                    .options(selectinload(Image.tags))
                ).scalars():
                    self._grid.set_item_tags(img)
            if group_ids:
                for grp in session.execute(
                    select(Group).where(Group.id.in_(group_ids))
                    .options(selectinload(Group.tags))
                ).scalars():
                    self._grid.set_item_tags(grp)
        self._detail.set_completion_list(all_t)
        self._detail.set_color_map(cmap)
        self._search_bar.set_completion_list(all_t)
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
        mode_label = "OR" if mode == "or" else "AND"
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

    def _toggle_theme(self, checked: bool) -> None:
        self._dark_mode = checked
        self._act_theme.setText("☀ ライトモード" if checked else "🌙 ダークモード")
        apply_theme(QApplication.instance(), self._dark_mode)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def _export_json(self) -> None:
        last_dir = self._settings().value("json_last_dir", "")
        default_path = str(Path(last_dir) / "pixella_export.json") if last_dir else "pixella_export.json"
        path, _ = QFileDialog.getSaveFileName(
            self, "JSONに書き出し", default_path, "JSON (*.json)"
        )
        if path:
            self._settings().setValue("json_last_dir", str(Path(path).parent))
            with get_session() as session:
                export_json(session, path)
            self._status.showMessage(f"書き出し完了: {path}", 5000)

    def _import_json(self) -> None:
        last_dir = self._settings().value("json_last_dir", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "JSONから読み込み", last_dir, "JSON (*.json)"
        )
        if not path:
            return
        self._settings().setValue("json_last_dir", str(Path(path).parent))
        # バリデーション
        try:
            import json as _json
            raw = Path(path).read_text(encoding="utf-8")
            data = _json.loads(raw)
            from pixella.db.repository import _validate_import_json
            _validate_import_json(data)
        except Exception as e:
            QMessageBox.critical(self, "読み込みエラー", f"JSONの検証に失敗しました:\n{e}")
            return
        # 確認ダイアログ
        n_images = len(data.get("images", []))
        n_groups = len(data.get("groups", []))
        n_tags   = len(data.get("tags", []))
        reply = QMessageBox.warning(
            self,
            "データの読み込み確認",
            f"現在のデータはすべて削除されます。本当に読み込みますか？\n\n"
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
