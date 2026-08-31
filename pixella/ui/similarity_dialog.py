"""類似画像検出ダイアログ (設定 → 進捗 → 結果 の 3 フェーズ)。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QObject, QRunnable, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)
from PIL import Image as PILImage
from sqlalchemy import select

from pixella.core.similarity import PRESETS, Preset, find_similar_clusters, hex_to_hash
from pixella.core.workers import HashWorkerPool, ThumbnailWorkerPool
from pixella.db import get_session, all_images
from pixella.db.models import Image
from pixella.db.repository import (
    add_similar_ignore, all_similar_ignores, create_group, delete_similar_ignores,
    prune_similar_ignores, remove_image, remove_image_from_group, save_image_hashes,
    similar_ignore_sets,
)
from pixella.ui.dialogs import GroupDialog, suggest_group_name

MAX_SETS = 200          # 結果画面に表示する類似セットの上限
CARDS_PER_ROW = 4
THUMB_PX = 140
SAVE_BATCH = 200        # ハッシュを DB へまとめ書きする件数
IGNORE_THUMB_PX = 56    # 非表示指定の管理ダイアログのサムネイルサイズ
IGNORE_MAX_THUMBS = 10  # 1 件あたりに並べるサムネイルの上限


def _fmt_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


class _ClusterSignals(QObject):
    # (run_gen, [[カード情報 dict, ...], ...], 非表示指定で除外したセット数)
    done = Signal(int, list, int)


class _ClusterTask(QRunnable):
    """クラスタリングと結果表示用メタデータ収集をバックグラウンドで行う。"""

    def __init__(self, gen: int, records: list[dict], preset: Preset,
                 ignored: set[frozenset[int]]) -> None:
        super().__init__()
        self._gen = gen
        self._records = records
        self._preset = preset
        self._ignored = ignored
        self.signals = _ClusterSignals()
        # autoDelete だと run() 直後にワーカースレッド側で Python ラッパーごと破棄され、
        # メインスレッド所属の signals がスレッド違反で破棄されてクラッシュする
        # （Hash/Thumbnail タスクと同じパターン）。参照は SimilarityDialog が保持し、
        # シグナル配送後にメインスレッドで解放する。
        self.setAutoDelete(False)

    def run(self) -> None:  # type: ignore[override]
        entries = [
            (r["id"], hex_to_hash(r["phash"]), hex_to_hash(r["dhash"]), r["gray"])
            for r in self._records
        ]
        clusters = find_similar_clusters(entries, self._preset)
        # 「今後表示しない」と指定された組み合わせに丸ごと含まれるセットを除外する
        # (完全一致もその一種。指定より画像が増えたセットは別物として残す)。
        # 上限で切る前に除外しないと、非表示セットが表示枠を消費してしまう。
        hidden = 0
        if self._ignored:
            kept = []
            for cluster in clusters:
                if any(frozenset(cluster) <= ig for ig in self._ignored):
                    hidden += 1
                else:
                    kept.append(cluster)
            clusters = kept
        clusters = clusters[:MAX_SETS]
        by_id = {r["id"]: r for r in self._records}
        result: list[list[dict]] = []
        for cluster in clusters:
            cards = []
            for image_id in cluster:
                rec = dict(by_id[image_id])
                # 解像度・ファイルサイズはセット入りした画像のみ取得 (ヘッダ読みなので軽量)
                try:
                    with PILImage.open(rec["path"]) as im:
                        rec["width"], rec["height"] = im.size
                    rec["filesize"] = os.path.getsize(rec["path"])
                except Exception:
                    rec["width"] = rec["height"] = rec["filesize"] = 0
                cards.append(rec)
            # 「残す」候補 (高解像度・大サイズ) が先頭に来るよう並べる
            cards.sort(key=lambda r: (r["width"] * r["height"], r["filesize"]), reverse=True)
            result.append(cards)
        self.signals.done.emit(self._gen, result, hidden)


class _ImageCard(QFrame):
    """結果画面の画像カード (チェックボックス + サムネイル + メタ情報)。"""

    def __init__(self, info: dict, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("similarCard")
        self.info = info
        self.removed = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.checkbox = QCheckBox(info["filename"])
        self.checkbox.setToolTip(info["path"])
        layout.addWidget(self.checkbox)

        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_PX, THUMB_PX)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignHCenter)

        if info["width"] and info["height"]:
            meta = f'{info["width"]}×{info["height"]} · {_fmt_size(info["filesize"])}'
        else:
            meta = _fmt_size(info["filesize"]) if info["filesize"] else "—"
        meta_label = QLabel(meta)
        meta_label.setObjectName("similarCardInfo")
        meta_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(meta_label)

        if info.get("group_name"):
            badge = QLabel(f'📁 {info["group_name"]}')
            badge.setObjectName("similarCardBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(badge)

        self.setToolTip(info["path"])
        self.setFixedWidth(THUMB_PX + 40)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # カードのどこをクリックしてもチェックをトグルできるようにする
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.toggle()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        # ダブルクリックで OS 既定のプログラムで画像を開く
        if event.button() == Qt.MouseButton.LeftButton:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.info["path"]))
        super().mouseDoubleClickEvent(event)

    def set_thumbnail(self, thumb_path: str) -> None:
        pix = QPixmap(thumb_path)
        if not pix.isNull():
            self.thumb.setPixmap(pix.scaled(
                THUMB_PX, THUMB_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class SimilarityDialog(QDialog):
    """類似画像検出のフロー全体 (プリセット選択 → ハッシュ計算 → 結果操作) を担うダイアログ。"""

    content_changed = Signal()  # 削除・グループ化などで一覧の再描画が必要になったとき

    def __init__(self, thumb_pool: ThumbnailWorkerPool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("similarityDialog")
        self.setWindowTitle("類似画像を検出")
        self.setMinimumSize(420, 260)
        self.resize(460, 300)

        self._thumb_pool = thumb_pool
        self._hash_pool: HashWorkerPool | None = None
        self._gen = 0                     # 実行世代 (キャンセル後の遅延シグナルを無視するため)
        self._records: dict[int, dict] = {}
        self._pending_rows: list[tuple[int, str, str, bytes, float]] = []
        self._hash_total = 0
        self._hash_done = 0
        self._skipped = 0
        self._cards: dict[int, _ImageCard] = {}
        self._set_frames: list[tuple[QFrame, list[_ImageCard]]] = []
        self._cluster_tasks: set[_ClusterTask] = set()  # setAutoDelete(False) タスクの参照保持
        self._ignored_sets: set[frozenset[int]] = set()  # 検出開始時に読み込む非表示指定
        self._result_base_text = ""     # 結果ラベルの基本文 (件数サマリ)
        self._ignored_now = 0           # この結果画面で新たに非表示指定にしたセット数

        layout = QVBoxLayout(self)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack)
        self._stack.addWidget(self._build_setup_page())
        self._stack.addWidget(self._build_progress_page())
        self._stack.addWidget(self._build_results_page())
        self._refresh_ignore_summary()

    # ------------------------------------------------------------------
    # ページ構築
    # ------------------------------------------------------------------

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("検出の厳しさを選択してください:"))

        self._preset_radios: list[QRadioButton] = []
        for i, preset in enumerate(PRESETS):
            radio = QRadioButton(f"{preset.label} — {preset.description}")
            if i == 0:  # 厳密をデフォルトに
                radio.setChecked(True)
            self._preset_radios.append(radio)
            layout.addWidget(radio)

        layout.addSpacing(8)
        self._include_grouped = QCheckBox("グループ内の画像も対象に含める")
        self._include_grouped.setChecked(True)
        layout.addWidget(self._include_grouped)

        layout.addSpacing(8)
        ignore_row = QHBoxLayout()
        self._ignore_summary = QLabel("")
        self._ignore_summary.setObjectName("similarCardInfo")
        ignore_row.addWidget(self._ignore_summary)
        ignore_row.addStretch(1)
        self._manage_ignores_btn = QPushButton("非表示指定の管理…")
        self._manage_ignores_btn.clicked.connect(self._open_ignore_manager)
        ignore_row.addWidget(self._manage_ignores_btn)
        layout.addLayout(ignore_row)

        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        start_btn = QPushButton("検出開始")
        start_btn.setDefault(True)
        start_btn.clicked.connect(self._start_detection)
        btn_row.addWidget(start_btn)
        close_btn = QPushButton("キャンセル")
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        return page

    def _build_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addStretch(1)
        self._progress_label = QLabel("")
        self._progress_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        layout.addWidget(self._progress_bar)
        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self._cancel_detection)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        return page

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self._result_label = QLabel("")
        layout.addWidget(self._result_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_body = QWidget()
        self._sets_layout = QVBoxLayout(self._scroll_body)
        self._sets_layout.addStretch(1)
        self._scroll.setWidget(self._scroll_body)
        layout.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout()
        retry_btn = QPushButton("再検出…")
        retry_btn.clicked.connect(self._back_to_setup)
        btn_row.addWidget(retry_btn)
        auto_btn = QPushButton("小さい方を自動チェック")
        auto_btn.clicked.connect(self._auto_check_smaller)
        btn_row.addWidget(auto_btn)
        clear_btn = QPushButton("チェック全解除")
        clear_btn.clicked.connect(self._clear_checks)
        btn_row.addWidget(clear_btn)
        self._group_btn = QPushButton("チェックをグループ化…")
        self._group_btn.clicked.connect(self._group_checked)
        btn_row.addWidget(self._group_btn)
        btn_row.addStretch(1)
        self._delete_btn = QPushButton("完全に削除")
        self._delete_btn.clicked.connect(lambda: self._remove_checked(delete_files=True))
        btn_row.addWidget(self._delete_btn)
        self._remove_btn = QPushButton("管理から除外")
        self._remove_btn.clicked.connect(lambda: self._remove_checked(delete_files=False))
        btn_row.addWidget(self._remove_btn)
        close_btn = QPushButton("閉じる")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        return page

    # ------------------------------------------------------------------
    # 検出フロー
    # ------------------------------------------------------------------

    def _back_to_setup(self) -> None:
        self.setMinimumSize(420, 260)
        self.resize(460, 300)
        self._refresh_ignore_summary()
        self._stack.setCurrentIndex(0)

    def _selected_preset(self) -> Preset:
        for radio, preset in zip(self._preset_radios, PRESETS):
            if radio.isChecked():
                return preset
        return PRESETS[1]

    def _start_detection(self) -> None:
        self._gen += 1
        self._records = {}
        self._pending_rows = []
        self._hash_done = 0
        self._skipped = 0
        include_grouped = self._include_grouped.isChecked()

        # メンバーが失われた指定 (もう再検出されない) を掃除してから読み込む
        prune_similar_ignores()
        with get_session() as session:
            images = all_images(session)
            self._ignored_sets = similar_ignore_sets(session)

        to_hash: list[dict] = []
        for img in images:
            if not include_grouped and img.group_id is not None:
                continue
            try:
                st = os.stat(img.path)
            except OSError:
                self._skipped += 1
                continue
            rec = {
                "id": img.id,
                "path": img.path,
                "filename": img.filename,
                "group_name": img.group.name if img.group is not None else None,
                "phash": img.phash,
                "dhash": img.dhash,
                "gray": img.gray,
            }
            self._records[img.id] = rec
            if not (img.phash and img.dhash and img.gray and img.hash_mtime == st.st_mtime):
                # 再計算対象 (未計算 or 旧形式 or ファイル更新)
                rec["phash"] = rec["dhash"] = rec["gray"] = None
                to_hash.append(rec)

        self._hash_total = len(to_hash)
        self._stack.setCurrentIndex(1)
        if not to_hash:
            self._start_clustering()
            return

        self._progress_bar.setRange(0, self._hash_total)
        self._progress_bar.setValue(0)
        self._update_progress_label()
        self._hash_pool = HashWorkerPool()
        gen = self._gen
        for rec in to_hash:
            self._hash_pool.request(
                rec["id"], rec["path"],
                lambda *args, g=gen: self._on_hash_done(g, *args),
                lambda image_id, g=gen: self._on_hash_error(g, image_id),
            )

    def _update_progress_label(self) -> None:
        text = f"ハッシュ計算中 {self._hash_done}/{self._hash_total}"
        if self._skipped:
            text += f"（スキップ: {self._skipped}）"
        self._progress_label.setText(text)

    def _on_hash_done(self, gen: int, image_id: int, phash: str, dhash: str,
                      gray: bytes, mtime: float, width: int, height: int,
                      filesize: int) -> None:
        if gen != self._gen:
            return
        rec = self._records.get(image_id)
        if rec is not None:
            rec["phash"] = phash
            rec["dhash"] = dhash
            rec["gray"] = gray
        self._pending_rows.append((image_id, phash, dhash, gray, mtime))
        if len(self._pending_rows) >= SAVE_BATCH:
            self._flush_pending_rows()
        self._advance_hash_progress()

    def _on_hash_error(self, gen: int, image_id: int) -> None:
        if gen != self._gen:
            return
        self._records.pop(image_id, None)
        self._skipped += 1
        self._advance_hash_progress()

    def _advance_hash_progress(self) -> None:
        self._hash_done += 1
        self._progress_bar.setValue(self._hash_done)
        self._update_progress_label()
        if self._hash_done >= self._hash_total:
            self._flush_pending_rows()
            self._start_clustering()

    def _flush_pending_rows(self) -> None:
        if self._pending_rows:
            save_image_hashes(self._pending_rows)
            self._pending_rows = []

    def _cancel_detection(self) -> None:
        self._gen += 1  # 遅延シグナルを無視する
        if self._hash_pool is not None:
            self._hash_pool.cancel_all()
            self._hash_pool = None
        self._flush_pending_rows()
        self._stack.setCurrentIndex(0)

    def _start_clustering(self) -> None:
        self._hash_pool = None
        self._progress_label.setText("比較中…")
        self._progress_bar.setRange(0, 0)  # インデターミネート
        valid = [r for r in self._records.values() if r["phash"] and r["dhash"] and r["gray"]]
        task = _ClusterTask(self._gen, valid, self._selected_preset(), self._ignored_sets)
        task.signals.done.connect(self._on_clusters_ready)
        self._cluster_tasks.add(task)
        task.signals.done.connect(lambda *_a, t=task: self._cluster_tasks.discard(t))
        QThreadPool.globalInstance().start(task)

    # ------------------------------------------------------------------
    # 結果表示
    # ------------------------------------------------------------------

    def _on_clusters_ready(self, gen: int, clusters: list[list[dict]],
                           hidden: int) -> None:
        if gen != self._gen:
            return
        # 前回結果をクリア
        for frame, _cards in self._set_frames:
            frame.setParent(None)
            frame.deleteLater()
        self._set_frames = []
        self._cards = {}
        self._ignored_now = 0

        total_imgs = sum(len(c) for c in clusters)
        if clusters:
            text = f"{len(clusters)} セット / {total_imgs} 枚の類似画像が見つかりました"
            if len(clusters) >= MAX_SETS:
                text += f"（上位 {MAX_SETS} セットのみ表示）"
            if self._skipped:
                text += f" — スキップ: {self._skipped} 件"
        else:
            text = "類似画像は見つかりませんでした"
            if self._skipped:
                text += f"（スキップ: {self._skipped} 件）"
        if hidden:
            text += f" — 非表示指定により {hidden} セットを除外"
        self._result_base_text = text
        self._update_result_label()

        for set_idx, cluster in enumerate(clusters, start=1):
            frame = QFrame()
            frame.setObjectName("similarSetFrame")
            v = QVBoxLayout(frame)
            header_row = QHBoxLayout()
            header = QLabel(f"セット {set_idx}（{len(cluster)}枚）")
            header.setObjectName("similarSetHeader")
            header_row.addWidget(header)
            header_row.addStretch(1)
            ignore_btn = QPushButton("今後表示しない")
            ignore_btn.setToolTip(
                "この組み合わせと、その一部だけが検出されたセットを\n"
                "次回以降の検出結果に表示しません。\n"
                "画像が増えた組み合わせは別物として表示されます。\n"
                "解除は設定画面の「非表示指定の管理…」から行えます。"
            )
            # append より前なので len(self._set_frames) がこのセットの添字になる
            ignore_btn.clicked.connect(
                lambda _checked=False, i=len(self._set_frames): self._ignore_set(i))
            header_row.addWidget(ignore_btn)
            v.addLayout(header_row)
            grid = QGridLayout()
            grid.setSpacing(6)
            cards: list[_ImageCard] = []
            for i, info in enumerate(cluster):
                card = _ImageCard(info)
                grid.addWidget(card, i // CARDS_PER_ROW, i % CARDS_PER_ROW,
                               Qt.AlignmentFlag.AlignLeft)
                cards.append(card)
                self._cards[info["id"]] = card
                self._thumb_pool.request(info["id"], info["path"], self._on_thumb_done)
            grid.setColumnStretch(CARDS_PER_ROW, 1)
            v.addLayout(grid)
            self._sets_layout.insertWidget(self._sets_layout.count() - 1, frame)
            self._set_frames.append((frame, cards))

        self._remove_btn.setEnabled(bool(clusters))
        self._delete_btn.setEnabled(bool(clusters))
        self._group_btn.setEnabled(bool(clusters))
        self._stack.setCurrentIndex(2)
        # 結果一覧は横に画像カードを並べるため、設定画面より広い表示領域が要る
        # (削除ボタン追加分だけ従来より幅を広げている)
        self.setMinimumSize(900, 520)
        if self.width() < 940 or self.height() < 560:
            self.resize(940, 560)

    def _on_thumb_done(self, image_id: int, thumb_path: str) -> None:
        card = self._cards.get(image_id)
        if card is not None:
            card.set_thumbnail(thumb_path)

    # ------------------------------------------------------------------
    # 結果操作
    # ------------------------------------------------------------------

    def _auto_check_smaller(self) -> None:
        """各セットで最高解像度 (同点なら最大サイズ) の 1 枚を残し、他をチェックする。"""
        for _frame, cards in self._set_frames:
            alive = [c for c in cards if not c.removed]
            if len(alive) < 2:
                continue
            best = max(alive, key=lambda c: (c.info["width"] * c.info["height"],
                                             c.info["filesize"]))
            for card in alive:
                card.checkbox.setChecked(card is not best)

    def _clear_checks(self) -> None:
        for card in self._cards.values():
            card.checkbox.setChecked(False)

    def _ignore_set(self, index: int) -> None:
        """このセットの組み合わせを次回以降の検出結果に出さないよう登録する。"""
        frame, cards = self._set_frames[index]
        alive = [c for c in cards if not c.removed]
        if len(alive) < 2:
            frame.setVisible(False)
            return
        image_ids = [c.info["id"] for c in alive]
        add_similar_ignore(image_ids)
        # DB 側の正規化 (包含される指定は極大な指定に統一) に手元の集合も合わせる
        new = frozenset(image_ids)
        if not any(new <= ig for ig in self._ignored_sets):
            self._ignored_sets = {ig for ig in self._ignored_sets if not ig < new}
            self._ignored_sets.add(new)
        self._ignored_now += 1
        # カードを処理済みにするとセット枠ごと非表示になる (ライブラリは変更しない)
        self._hide_processed(alive, notify=False)

    def _update_result_label(self) -> None:
        remaining = sum(
            1 for _frame, cards in self._set_frames
            if sum(1 for c in cards if not c.removed) >= 2
        )
        if self._set_frames and remaining == 0:
            text = "すべてのセットを処理しました"
        else:
            text = self._result_base_text
        if self._ignored_now:
            text += f"（{self._ignored_now} セットを非表示指定にしました）"
        self._result_label.setText(text)

    def _refresh_ignore_summary(self) -> None:
        """設定ページの非表示指定件数を DB から読み直して表示する。"""
        with get_session() as session:
            count = len(all_similar_ignores(session))
        self._ignore_summary.setText(
            f"非表示指定: {count} 件" if count else "非表示指定: なし")
        self._manage_ignores_btn.setEnabled(count > 0)

    def _open_ignore_manager(self) -> None:
        dlg = IgnoredSetsDialog(self._thumb_pool, parent=self)
        dlg.exec()
        self._refresh_ignore_summary()

    def _remove_checked(self, delete_files: bool) -> None:
        checked = [c for c in self._cards.values()
                   if not c.removed and c.checkbox.isChecked()]
        if not checked:
            QMessageBox.information(self, "類似画像を検出", "除外する画像がチェックされていません。")
            return
        if delete_files:
            msg = (f"{len(checked)} 件のファイルを完全に削除し、Pixellaの管理から除外しますか？\n"
                   "この操作は元に戻せません。")
        else:
            msg = (f"{len(checked)} 件をPixellaの管理から除外しますか？\n"
                   "(元のファイルは削除されません)")
        full_sets = sum(
            1 for _frame, cards in self._set_frames
            if (alive := [c for c in cards if not c.removed])
            and all(c.checkbox.isChecked() for c in alive)
        )
        if full_sets:
            msg += f"\n\n注意: セット内の全画像をチェックしているセットが {full_sets} 件あります。"
        if QMessageBox.question(
            self, "削除", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        failed: list[str] = []
        targets = checked
        if delete_files:
            targets = []
            for card in checked:
                try:
                    os.remove(card.info["path"])
                except OSError as e:
                    failed.append(f'{card.info["filename"]}: {e}')
                else:
                    targets.append(card)

        ids = [c.info["id"] for c in targets]
        with get_session() as session:
            for img in session.execute(select(Image).where(Image.id.in_(ids))).scalars():
                remove_image(session, img)
            session.commit()

        self._hide_processed(targets)

        if failed:
            QMessageBox.warning(
                self, "削除エラー",
                "以下のファイルは削除できなかったため、管理からも除外していません:\n"
                + "\n".join(failed),
            )

    def _group_checked(self) -> None:
        """チェック済みの画像をまとめて新しいグループにする。"""
        checked = [c for c in self._cards.values()
                   if not c.removed and c.checkbox.isChecked()]
        if len(checked) < 2:
            QMessageBox.information(
                self, "グループ化", "グループ化する画像を2枚以上チェックしてください。")
            return

        filenames = [c.info["filename"] for c in checked]
        dlg = GroupDialog(filenames, default_name=suggest_group_name(filenames), parent=self)
        if dlg.exec() != GroupDialog.DialogCode.Accepted:
            return

        ids = [c.info["id"] for c in checked]
        with get_session() as session:
            # 既に別グループに属している画像は、新グループへ移す前に一旦外す
            for img in session.execute(select(Image).where(Image.id.in_(ids))).scalars():
                if img.group_id is not None:
                    remove_image_from_group(session, img)
            create_group(session, dlg.group_name, ids)
            session.commit()

        self._hide_processed(checked)

    def _hide_processed(self, targets: list[_ImageCard], notify: bool = True) -> None:
        """削除・グループ化などで処理済みになったカードを一覧から隠す。

        notify=False は非表示指定のようにライブラリ側を変更しない操作で使う
        (メインウィンドウの再描画は不要)。
        """
        for card in targets:
            card.removed = True
            card.setVisible(False)
            self._cards.pop(card.info["id"], None)
        # 残り 1 枚以下になったセットは非表示にする
        remaining_sets = 0
        for frame, cards in self._set_frames:
            alive = [c for c in cards if not c.removed]
            if len(alive) < 2:
                frame.setVisible(False)
                for c in alive:
                    self._cards.pop(c.info["id"], None)
                    c.removed = True
            else:
                remaining_sets += 1
        self._update_result_label()
        if remaining_sets == 0:
            self._remove_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            self._group_btn.setEnabled(False)
        if notify and targets:
            self.content_changed.emit()

    # ------------------------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        # アルバム切り替え後などに件数がずれないよう、表示のたび読み直す
        if self._stack.currentIndex() == 0:
            self._refresh_ignore_summary()
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._gen += 1
        if self._hash_pool is not None:
            self._hash_pool.cancel_all()
            self._hash_pool = None
        self._flush_pending_rows()
        super().closeEvent(event)


class _IgnoredSetRow(QFrame):
    """非表示指定 1 件分の行 (チェックボックス + メンバーのサムネイル)。"""

    def __init__(self, ignore_id: int, members: list[dict], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ignoredSetRow")
        self.ignore_id = ignore_id
        self._thumbs: dict[int, QLabel] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self.checkbox = QCheckBox(f"{len(members)} 枚")
        self.checkbox.setToolTip("\n".join(m["path"] for m in members))
        layout.addWidget(self.checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        for member in members[:IGNORE_MAX_THUMBS]:
            thumb = QLabel()
            thumb.setFixedSize(IGNORE_THUMB_PX, IGNORE_THUMB_PX)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb.setToolTip(f'{member["filename"]}\n{member["path"]}')
            layout.addWidget(thumb)
            self._thumbs[member["id"]] = thumb
        if len(members) > IGNORE_MAX_THUMBS:
            more = QLabel(f"他 {len(members) - IGNORE_MAX_THUMBS} 枚")
            more.setObjectName("similarCardInfo")
            layout.addWidget(more)
        layout.addStretch(1)

    def image_ids(self) -> list[int]:
        return list(self._thumbs)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        # 行のどこをクリックしてもチェックをトグルできるようにする
        if event.button() == Qt.MouseButton.LeftButton:
            self.checkbox.toggle()
        super().mousePressEvent(event)

    def set_thumbnail(self, image_id: int, thumb_path: str) -> None:
        thumb = self._thumbs.get(image_id)
        if thumb is None:
            return
        pix = QPixmap(thumb_path)
        if not pix.isNull():
            thumb.setPixmap(pix.scaled(
                IGNORE_THUMB_PX, IGNORE_THUMB_PX,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))


class IgnoredSetsDialog(QDialog):
    """「今後表示しない」と指定した組み合わせの一覧・解除ダイアログ。"""

    def __init__(self, thumb_pool: ThumbnailWorkerPool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ignoredSetsDialog")
        self.setWindowTitle("非表示指定の管理")
        self.setMinimumSize(560, 400)
        self.resize(680, 480)

        self._thumb_pool = thumb_pool
        self._rows: list[_IgnoredSetRow] = []
        # 遅延到着したサムネイルを解決するための現在行マップ (再読込で差し替える)
        self._rows_by_image: dict[int, list[_IgnoredSetRow]] = {}

        layout = QVBoxLayout(self)
        hint = QLabel(
            "登録した組み合わせと、その一部だけが検出されたセットは\n"
            "類似画像の検出結果に表示されません。\n"
            "画像が増えた組み合わせは別物として扱われ、表示されます。"
        )
        hint.setObjectName("similarCardInfo")
        layout.addWidget(hint)

        self._count_label = QLabel("")
        layout.addWidget(self._count_label)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.addStretch(1)
        self._scroll.setWidget(self._body)
        layout.addWidget(self._scroll, 1)

        btn_row = QHBoxLayout()
        self._delete_btn = QPushButton("チェックした指定を解除")
        self._delete_btn.clicked.connect(self._delete_checked)
        btn_row.addWidget(self._delete_btn)
        clear_btn = QPushButton("チェック全解除")
        clear_btn.clicked.connect(
            lambda: [row.checkbox.setChecked(False) for row in self._rows])
        btn_row.addWidget(clear_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("閉じる")
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reload()

    # ------------------------------------------------------------------

    def _reload(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        self._rows_by_image = {}

        # メンバーが失われた指定は表示前に掃除する (もう再検出されない)
        prune_similar_ignores()
        with get_session() as session:
            entries = all_similar_ignores(session)
            image_ids = {i for _ignore_id, ids in entries for i in ids}
            info = {
                img.id: {"id": img.id, "path": img.path, "filename": img.filename}
                for img in session.execute(
                    select(Image).where(Image.id.in_(image_ids))
                ).scalars()
            }

        for ignore_id, ids in entries:
            members = [info[i] for i in ids if i in info]
            if not members:
                continue
            row = _IgnoredSetRow(ignore_id, members)
            self._body_layout.insertWidget(self._body_layout.count() - 1, row)
            self._rows.append(row)
            for member in members[:IGNORE_MAX_THUMBS]:
                self._rows_by_image.setdefault(member["id"], []).append(row)
                self._thumb_pool.request(member["id"], member["path"], self._on_thumb_done)

        self._count_label.setText(
            f"{len(self._rows)} 件の組み合わせを非表示にしています"
            if self._rows else "非表示指定はありません"
        )
        self._delete_btn.setEnabled(bool(self._rows))

    def _on_thumb_done(self, image_id: int, thumb_path: str) -> None:
        # 再読込後に届いた古い行への配送は _rows_by_image の差し替えで自然に落ちる
        for row in self._rows_by_image.get(image_id, []):
            row.set_thumbnail(image_id, thumb_path)

    def _delete_checked(self) -> None:
        checked = [row for row in self._rows if row.checkbox.isChecked()]
        if not checked:
            QMessageBox.information(
                self, "非表示指定の管理", "解除する指定がチェックされていません。")
            return
        delete_similar_ignores([row.ignore_id for row in checked])
        self._reload()
