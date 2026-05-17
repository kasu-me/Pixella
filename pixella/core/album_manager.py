"""Album manager — manages multiple named SQLite databases (one per album)."""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import NamedTuple

from pixella.core.config import ALBUMS_DIR, DATA_DIR, DB_PATH


class AlbumInfo(NamedTuple):
    name: str
    db_file: str  # filename relative to ALBUMS_DIR


_META_FILE = DATA_DIR / "albums_meta.json"


class AlbumManager:
    """Manages album metadata and active-album state.

    Albums are stored as individual SQLite files under ALBUMS_DIR.
    Metadata (names, active album) is persisted in albums_meta.json.
    """

    def __init__(self) -> None:
        self._albums: list[AlbumInfo] = []
        self._active_name: str = ""
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if _META_FILE.exists():
            try:
                raw = _META_FILE.read_text(encoding="utf-8")
                data = json.loads(raw)
                self._albums = [
                    AlbumInfo(a["name"], a["db_file"])
                    for a in data.get("albums", [])
                ]
                self._active_name = data.get("active", "")
            except Exception:
                self._albums = []
                self._active_name = ""
        # アクティブアルバムが存在しない場合は先頭に戻す
        if self._active_name not in {a.name for a in self._albums}:
            self._active_name = self._albums[0].name if self._albums else ""

    def _save(self) -> None:
        data = {
            "albums": [{"name": a.name, "db_file": a.db_file} for a in self._albums],
            "active": self._active_name,
        }
        _META_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # First-run migration
    # ------------------------------------------------------------------

    def ensure_initialized(self) -> None:
        """初回起動時: 既存の pixella.db をデフォルトアルバムとして取り込む。

        albums_meta.json が存在しない場合のみ実行される。
        """
        if self._albums:
            return  # already initialized

        default_name = "デフォルト"
        db_file = f"{uuid.uuid4().hex}.db"
        dest = ALBUMS_DIR / db_file

        if DB_PATH.exists():
            # 既存の DB をアルバムディレクトリにコピー
            shutil.copy2(str(DB_PATH), str(dest))
        # DB_PATH が存在しない場合は空の DB ファイルとして init_db が生成する

        self._albums = [AlbumInfo(default_name, db_file)]
        self._active_name = default_name
        self._save()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def active_name(self) -> str:
        return self._active_name

    @property
    def album_names(self) -> list[str]:
        return [a.name for a in self._albums]

    # ------------------------------------------------------------------
    # DB path resolution
    # ------------------------------------------------------------------

    def get_db_path(self, name: str) -> Path:
        for a in self._albums:
            if a.name == name:
                return ALBUMS_DIR / a.db_file
        raise KeyError(f"Album not found: {name!r}")

    def active_db_path(self) -> Path:
        return self.get_db_path(self._active_name)

    # ------------------------------------------------------------------
    # Album CRUD
    # ------------------------------------------------------------------

    def create_album(self, name: str) -> Path:
        """新規アルバムを作成し、そのDBファイルパスを返す。"""
        name = name.strip()
        if not name:
            raise ValueError("アルバム名を入力してください")
        if name in {a.name for a in self._albums}:
            raise ValueError(f"アルバム '{name}' は既に存在します")
        db_file = f"{uuid.uuid4().hex}.db"
        self._albums.append(AlbumInfo(name, db_file))
        self._save()
        return ALBUMS_DIR / db_file

    def rename_album(self, old_name: str, new_name: str) -> None:
        """アルバム名を変更する。"""
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("アルバム名を入力してください")
        if new_name == old_name:
            return
        if new_name in {a.name for a in self._albums}:
            raise ValueError(f"アルバム '{new_name}' は既に存在します")
        self._albums = [
            AlbumInfo(new_name, a.db_file) if a.name == old_name else a
            for a in self._albums
        ]
        if self._active_name == old_name:
            self._active_name = new_name
        self._save()

    def delete_album(self, name: str) -> None:
        """アルバムを削除する。最後の1件は削除不可。"""
        if len(self._albums) <= 1:
            raise ValueError("最後のアルバムは削除できません")
        db_path = self.get_db_path(name)
        self._albums = [a for a in self._albums if a.name != name]
        if self._active_name == name:
            self._active_name = self._albums[0].name
        self._save()
        try:
            db_path.unlink(missing_ok=True)
        except OSError:
            pass

    def set_active(self, name: str) -> None:
        """アクティブアルバムを切り替える。"""
        if name not in {a.name for a in self._albums}:
            raise KeyError(f"Album not found: {name!r}")
        self._active_name = name
        self._save()

    def all_db_paths(self) -> list[tuple[str, Path]]:
        """全アルバムの (名前, DBパス) リストを返す。"""
        return [(a.name, ALBUMS_DIR / a.db_file) for a in self._albums]
