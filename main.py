"""Pixella entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from pixella.core import AlbumManager
from pixella.db.models import init_db
from pixella.ui.main_window import MainWindow
from pixella.ui.themes import apply_theme


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Pixella")
    app.setOrganizationName("Pixella")

    # Resource path for bundled exe
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    icon_path = base / "resources" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    # アルバムマネージャーを初期化（初回は既存DBをデフォルトアルバムに移行）
    album_manager = AlbumManager()
    album_manager.ensure_initialized()

    # アクティブなアルバムのDBを初期化
    init_db(album_manager.active_db_path())

    apply_theme(app, dark=False)

    window = MainWindow(album_manager)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
