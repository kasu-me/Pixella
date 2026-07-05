"""Background worker for generating thumbnails without blocking the UI."""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import QRunnable, QObject, Signal, QThreadPool

from .thumbnails import ThumbnailCache
from .similarity import compute_image_hashes, hash_to_hex


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
        # autoDelete だと run() 直後にワーカースレッド側で Python ラッパーごと破棄され、
        # メインスレッド所属の signals がスレッド違反で破棄されてクラッシュする。
        # 参照は ThumbnailWorkerPool が保持し、シグナル配送後にメインスレッドで解放する。
        self.setAutoDelete(False)

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
        self._tasks: set[ThumbnailTask] = set()

    def request(self, image_id: int, image_path: str, on_done, on_error=None) -> None:
        task = ThumbnailTask(image_id, image_path, self._cache)
        task.signals.done.connect(on_done)
        if on_error:
            task.signals.error.connect(on_error)
        self._tasks.add(task)
        task.signals.done.connect(lambda *_a, t=task: self._tasks.discard(t))
        task.signals.error.connect(lambda *_a, t=task: self._tasks.discard(t))
        self._pool.start(task)


class _HashSignals(QObject):
    # (image_id, phash_hex, dhash_hex, mtime, width, height, filesize)
    done = Signal(int, str, str, float, int, int, int)
    error = Signal(int)  # image_id


class HashTask(QRunnable):
    """類似検出用ハッシュを 1 画像分計算するタスク。

    画像を 1 回だけ開き、pHash/dHash に加えて結果画面の表示に使う
    解像度・ファイルサイズも同時に収集する。DB へは書かず Signal で返す。
    """

    def __init__(self, image_id: int, image_path: str, cancel: threading.Event) -> None:
        super().__init__()
        self._id = image_id
        self._path = image_path
        self._cancel = cancel
        self.signals = _HashSignals()
        # autoDelete だと run() 直後にワーカースレッド側で Python ラッパーごと破棄され、
        # メインスレッド所属の signals がスレッド違反で破棄されてクラッシュする。
        # 参照は HashWorkerPool が保持し、シグナル配送後にメインスレッドで解放する。
        self.setAutoDelete(False)

    def run(self) -> None:  # type: ignore[override]
        if self._cancel.is_set():
            return
        try:
            stat = os.stat(self._path)
            phash, dhash, width, height = compute_image_hashes(self._path)
        except Exception:
            self.signals.error.emit(self._id)
            return
        self.signals.done.emit(
            self._id, hash_to_hex(phash), hash_to_hex(dhash),
            stat.st_mtime, width, height, stat.st_size,
        )


class HashWorkerPool:
    def __init__(self, max_threads: int = 4) -> None:
        self._pool = QThreadPool.globalInstance()
        self._pool.setMaxThreadCount(max_threads)
        self.cancel_event = threading.Event()
        self._tasks: set[HashTask] = set()

    def request(self, image_id: int, image_path: str, on_done, on_error=None) -> None:
        task = HashTask(image_id, image_path, self.cancel_event)
        task.signals.done.connect(on_done)
        if on_error:
            task.signals.error.connect(on_error)
        self._tasks.add(task)
        task.signals.done.connect(lambda *_a, t=task: self._tasks.discard(t))
        task.signals.error.connect(lambda *_a, t=task: self._tasks.discard(t))
        self._pool.start(task)

    def cancel_all(self) -> None:
        """未実行タスクを打ち切る (実行中のタスクは完了まで待たず放置してよい)。"""
        self.cancel_event.set()
