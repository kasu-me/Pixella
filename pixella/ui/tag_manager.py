"""Tag manager dialog — lists all tags, allows search jump and deletion."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from pixella.db import get_session, all_tags_with_count, delete_tag, set_tag_color

# ソートキー定義: (key_name, ラベル)
_SORT_KEYS = [
    ("color",  "色"),
    ("name",   "タグ名"),
    ("count",  "枚数"),
]
_COLOR_COL_W = 36   # 色列の共通幅 (ヘッダーボタン + データ行コンテナ)


class TagManagerDialog(QDialog):
    """
    非モーダルダイアログ。
    タグ名クリック → search_requested(tag_name) シグナルを発火。
    削除ボタン → DB から対象タグを削除後、リストを更新。
    """

    search_requested = Signal(str)   # クリックされたタグ名
    color_changed    = Signal()       # タグ色が変更されたとき

    # ダイアログを閉じて再度開いてもソート順を維持するためクラス変数で保持
    _persist_sort_key: str = "name"
    _persist_sort_desc: bool = False

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("タグ管理")
        self.setMinimumSize(380, 480)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._sort_key: str = TagManagerDialog._persist_sort_key
        self._sort_desc: bool = TagManagerDialog._persist_sort_desc

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 8)
        root.setSpacing(4)

        # ヘッダー行 — データ行と同じ構造で列を揃える
        # データ行: margins(6,3,6,3) spacing(8)
        #   [color_btn:20] [tag_name:expanding] [count:50] [del_btn:24]
        self._header_row = QWidget()
        self._header_row.setObjectName("tagManagerHeader")
        self._header_layout = QHBoxLayout(self._header_row)
        self._header_layout.setContentsMargins(6, 2, 6, 2)
        self._header_layout.setSpacing(8)
        self._header_btns: dict[str, QPushButton] = {}

        # 「色」ボタン — _COLOR_COL_W固定 (「色▲」など矏印」が入る幅)
        btn_color = QPushButton("色")
        btn_color.setObjectName("tagManagerSortBtn")
        btn_color.setFlat(True)
        btn_color.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_color.setFixedWidth(_COLOR_COL_W)
        btn_color.clicked.connect(lambda _, k="color": self._on_sort_click(k))
        self._header_layout.addWidget(btn_color)
        self._header_btns["color"] = btn_color

        # 「タグ名」ボタン — expanding (tag name label と同幅)
        btn_name = QPushButton("タグ名")
        btn_name.setObjectName("tagManagerSortBtn")
        btn_name.setFlat(True)
        btn_name.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        btn_name.setStyleSheet("text-align: left;")
        btn_name.clicked.connect(lambda _, k="name": self._on_sort_click(k))
        self._header_layout.addWidget(btn_name)
        self._header_btns["name"] = btn_name

        # 「枚数」ボタン — 幅50px (count label と同幅)
        btn_count = QPushButton("枚数")
        btn_count.setObjectName("tagManagerSortBtn")
        btn_count.setFlat(True)
        btn_count.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_count.setFixedWidth(50)
        btn_count.clicked.connect(lambda _, k="count": self._on_sort_click(k))
        self._header_layout.addWidget(btn_count)
        self._header_btns["count"] = btn_count

        # 削除ボタン列のスペーサー — 幅24px (del_btn と同幅)
        spacer = QWidget()
        spacer.setFixedWidth(24)
        self._header_layout.addWidget(spacer)

        root.addWidget(self._header_row)

        # スクロールエリア
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)

        # 閉じるボタン
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.close)
        root.addWidget(btns)

        self._refresh()

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def _on_sort_click(self, key: str) -> None:
        if self._sort_key == key:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_key = key
            self._sort_desc = False
        # クラス変数に保存して次回開いたときに引き継ぐ
        TagManagerDialog._persist_sort_key = self._sort_key
        TagManagerDialog._persist_sort_desc = self._sort_desc
        self._refresh()

    def _sort_key_func(self, item: tuple) -> object:
        tag, count = item
        if self._sort_key == "name":
            return tag.name.lower()
        elif self._sort_key == "count":
            return count
        else:  # color
            return tag.color or ""

    def _update_header_labels(self) -> None:
        for key, label in _SORT_KEYS:
            btn = self._header_btns[key]
            if key == self._sort_key:
                arrow = " ▼" if self._sort_desc else " ▲"
                btn.setText(label + arrow)
                btn.setProperty("active", True)
            else:
                btn.setText(label)
                btn.setProperty("active", False)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """DBからタグ一覧を再取得してリストを再描画する。"""
        with get_session() as session:
            tag_rows = all_tags_with_count(session)

        tag_rows = sorted(tag_rows, key=self._sort_key_func, reverse=self._sort_desc)
        self._update_header_labels()

        container = QWidget()
        container.setObjectName("tagManagerContainer")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        if not tag_rows:
            empty = QLabel("タグはまだありません")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setObjectName("tagManagerEmpty")
            layout.addWidget(empty)
        else:
            for tag, count in tag_rows:
                row = self._make_row(tag.id, tag.name, tag.color, count)
                layout.addWidget(row)

        layout.addStretch()
        self._scroll.setWidget(container)

    def _make_row(self, tag_id: int, name: str, color: str | None, count: int) -> QWidget:
        row = QWidget()
        row.setObjectName("tagManagerRow")
        h = QHBoxLayout(row)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(8)

        # カラー丸ボタン — _COLOR_COL_W幅のコンテナに20pxボタンを中央配置
        color_wrap = QWidget()
        color_wrap.setFixedWidth(_COLOR_COL_W)
        cw_layout = QHBoxLayout(color_wrap)
        cw_layout.setContentsMargins(0, 0, 0, 0)
        cw_layout.setSpacing(0)
        color_btn = QPushButton()
        color_btn.setObjectName("tagManagerColor")
        color_btn.setFixedSize(20, 20)
        color_btn.setToolTip("タグの色を変更")
        self._apply_color_btn_style(color_btn, color)
        color_btn.clicked.connect(lambda _, tid=tag_id, n=name, btn=color_btn: self._on_color(tid, n, btn))
        cw_layout.addStretch()
        cw_layout.addWidget(color_btn)
        cw_layout.addStretch()
        h.addWidget(color_wrap)

        # タグ名（クリック可能なリンクラベル）
        lbl = QLabel(f'<a href="#">{name}</a>')
        lbl.setObjectName("tagManagerLink")
        lbl.setToolTip(f"「{name}」で検索")
        lbl.linkActivated.connect(lambda _, n=name: self._on_tag_clicked(n))
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(lbl)

        # 枚数バッジ
        count_lbl = QLabel(f"{count}件")
        count_lbl.setObjectName("tagManagerCount")
        count_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(count_lbl)

        # 削除ボタン
        del_btn = QPushButton("✕")
        del_btn.setObjectName("tagManagerDelete")
        del_btn.setFixedSize(24, 24)
        del_btn.setToolTip(f"タグ「{name}」を削除")
        del_btn.clicked.connect(lambda _, tid=tag_id, n=name: self._on_delete(tid, n))
        h.addWidget(del_btn)

        return row

    @staticmethod
    def _apply_color_btn_style(btn: QPushButton, color: str | None) -> None:
        if color:
            bg = QColor(color)
            luminance = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
            border = "#888" if luminance > 180 else color
            btn.setStyleSheet(
                f"background-color: {color}; border: 1px solid {border}; border-radius: 10px;"
            )
        else:
            btn.setStyleSheet(
                "background-color: transparent; border: 1px dashed #aaa; border-radius: 10px;"
            )

    def _on_tag_clicked(self, name: str) -> None:
        self.search_requested.emit(name)

    def _on_color(self, tag_id: int, name: str, btn: QPushButton) -> None:
        initial = QColor(btn.palette().button().color())
        chosen = QColorDialog.getColor(
            initial, self,
            f"「{name}」のタグ颜色を選択",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if not chosen.isValid():
            return
        # 「クリア」: アルファを0に誻わせた場合は None扩张
        color_str: str | None = chosen.name()  # "#rrggbb"
        with get_session() as session:
            set_tag_color(session, tag_id, color_str)
            session.commit()
        self._apply_color_btn_style(btn, color_str)
        self.color_changed.emit()

    def _on_delete(self, tag_id: int, name: str) -> None:
        reply = QMessageBox.question(
            self,
            "タグの削除",
            f"タグ「{name}」を削除しますか？\n"
            "このタグが付いた全ての画像・グループからも除去されます。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        with get_session() as session:
            delete_tag(session, tag_id)
            session.commit()

        self._refresh()
