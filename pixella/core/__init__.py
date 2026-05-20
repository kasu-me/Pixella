import re

from .config import APP_NAME, DATA_DIR, DB_PATH, THUMB_DIR, EXPORT_DIR, ALBUMS_DIR
from .thumbnails import ThumbnailCache, SUPPORTED_EXTS
from .workers import ThumbnailWorkerPool
from .album_manager import AlbumManager


def natural_sort_key(s: str) -> list:
    """自然順ソートのキー関数。数値部分を整数として比較する。

    例: file_2 < file_10 (通常の辞書順では file_10 < file_2 になる)
    """
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", s)
    ]


__all__ = [
    "APP_NAME", "DATA_DIR", "DB_PATH", "THUMB_DIR", "EXPORT_DIR", "ALBUMS_DIR",
    "ThumbnailCache", "SUPPORTED_EXTS",
    "ThumbnailWorkerPool",
    "AlbumManager",
    "natural_sort_key",
]
