"""Background worker for generating thumbnails without blocking the UI."""
from __future__ import annotations

from PySide6.QtCore import QRunnable, QObject, Signal, QThreadPool

from .thumbnails import ThumbnailCache


class _ThumbnailSignals(QObject):
    done = Signal(int, str)   # (image_id, thumb_path)
    error = Signal(int)       # image_id


class ThumbnailTask(QRunnable):
    def __init__(self, image_id: int, image_path: str, cache: ThumbnailCache) -> None:
        super().__init__()
        self._id = image_id
        self._path = image_path
        self._cache = cache
        self.signals = _ThumbnailSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # type: ignore[override]
        result = self._cache.ensure(self._path)
        if result:
            self.signals.done.emit(self._id, str(result))
        else:
            self.signals.error.emit(self._id)


class ThumbnailWorkerPool:
    def __init__(self, cache: ThumbnailCache, max_threads: int = 4) -> None:
        self._cache = cache
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(max_threads)

    def request(self, image_id: int, image_path: str, on_done, on_error=None) -> None:
        task = ThumbnailTask(image_id, image_path, self._cache)
        task.signals.done.connect(on_done)
        if on_error:
            task.signals.error.connect(on_error)
        self._pool.start(task)
