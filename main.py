"""Pixella entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from pixella.core.config import DB_PATH
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

    init_db(DB_PATH)
    apply_theme(app, dark=False)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
