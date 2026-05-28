"""Segoe Fluent Icons / Segoe MDL2 Assets helper for PySide6.

Windows には標準で以下のアイコンフォントがインストールされています。
  - Segoe Fluent Icons  (Windows 11 以降)
  - Segoe MDL2 Assets   (Windows 10 以降)

本モジュールはいずれかを自動選択し、グリフ文字から QIcon を生成します。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap


# ---------------------------------------------------------------------------
# Glyph code points (Segoe Fluent Icons / Segoe MDL2 Assets 共通)
# ---------------------------------------------------------------------------
class FluentGlyph:
    ADD        = "\uE710"   # Add (プラス)
    GROUP      = "\uF12B"   # Group (グループ化)
    UNGROUP    = "\uE8E6"   # Unpin (グループ解除)
    SEARCH     = "\uE721"   # Search / Zoom (検索)
    DELETE     = "\uE74D"   # Delete / Trash (削除)
    REFRESH    = "\uE72C"   # Sync / Refresh (再生成)
    BRIGHTNESS = "\uE793"   # Brightness / ライトモード切替
    MOON       = "\uE708"   # ダークモード切替


_PREFERRED_FONTS = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]


def _icon_font(pixel_size: int) -> QFont:
    """利用可能な優先フォントを返す。見つからない場合は先頭名で作成。"""
    available = set(QFontDatabase.families())
    for family in _PREFERRED_FONTS:
        if family in available:
            f = QFont(family)
            f.setPixelSize(pixel_size)
            return f
    # フォールバック: 見つからなくても名前で試みる
    f = QFont(_PREFERRED_FONTS[0])
    f.setPixelSize(pixel_size)
    return f


def make_fluent_icon(
    glyph: str,
    pixel_size: int = 20,
    color: QColor | None = None,
) -> QIcon:
    """Segoe Fluent Icons のグリフ文字を QIcon としてレンダリングする。

    Parameters
    ----------
    glyph:
        FluentGlyph の定数など、Segoe Fluent Icons / MDL2 の Unicode 文字。
    pixel_size:
        フォントのピクセルサイズ（= アイコンのおおよその高さ）。
    color:
        描画色。省略時はテーマに応じて呼び出し側で指定すること。
        未指定の場合は濃いグレー (#1F1F1F) を使用。
    """
    if color is None:
        color = QColor("#1F1F1F")

    # グリフの周囲に余白を取ったキャンバスサイズ
    canvas = pixel_size + 8
    pixmap = QPixmap(canvas, canvas)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setFont(_icon_font(pixel_size))
    painter.setPen(color)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()

    return QIcon(pixmap)
