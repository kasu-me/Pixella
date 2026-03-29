"""Thumbnail cache — generates and stores small previews on disk."""
from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image as PILImage

THUMB_SIZE = (200, 200)
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class ThumbnailCache:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, image_path: str) -> Path:
        digest = hashlib.sha256(image_path.encode()).hexdigest()
        return self.cache_dir / f"{digest}.png"

    def get(self, image_path: str) -> Path | None:
        """Return cached thumbnail path if it exists, else None."""
        p = self._cache_path(image_path)
        return p if p.exists() else None

    def generate(self, image_path: str) -> Path | None:
        """Generate thumbnail and return its path. Returns None on error."""
        cache_path = self._cache_path(image_path)
        if cache_path.exists():
            return cache_path
        try:
            with PILImage.open(image_path) as img:
                # For animated GIF/WebP use first frame
                if hasattr(img, "n_frames") and img.n_frames > 1:
                    img.seek(0)
                img = img.convert("RGBA")
                img.thumbnail(THUMB_SIZE, PILImage.LANCZOS)
                # Place on transparent canvas to preserve aspect ratio
                canvas = PILImage.new("RGBA", THUMB_SIZE, (0, 0, 0, 0))
                offset = (
                    (THUMB_SIZE[0] - img.width) // 2,
                    (THUMB_SIZE[1] - img.height) // 2,
                )
                canvas.paste(img, offset)
                canvas.save(cache_path, "PNG")
        except Exception:
            return None
        return cache_path

    def ensure(self, image_path: str) -> Path | None:
        """Return existing or newly generated thumbnail path."""
        return self.get(image_path) or self.generate(image_path)

    def invalidate(self, image_path: str) -> None:
        p = self._cache_path(image_path)
        if p.exists():
            p.unlink()
