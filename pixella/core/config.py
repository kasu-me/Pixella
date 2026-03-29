"""Application-wide settings and path resolution."""
from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Pixella"

# Data directory: %APPDATA%\Pixella  (Windows)
_appdata = os.environ.get("APPDATA", str(Path.home()))
DATA_DIR = Path(_appdata) / APP_NAME
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH    = DATA_DIR / "pixella.db"
THUMB_DIR  = DATA_DIR / "thumbnails"
EXPORT_DIR = DATA_DIR / "exports"

EXPORT_DIR.mkdir(parents=True, exist_ok=True)
