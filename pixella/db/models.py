"""SQLAlchemy models and database session management."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, String, Table, create_engine
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    __allow_unmapped__ = True


# ---------------------------------------------------------------------------
# Association tables (many-to-many)
# ---------------------------------------------------------------------------

image_tag_table = Table(
    "image_tag",
    Base.metadata,
    Column("image_id", Integer, ForeignKey("images.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",   Integer, ForeignKey("tags.id",   ondelete="CASCADE"), primary_key=True),
)

group_tag_table = Table(
    "group_tag",
    Base.metadata,
    Column("group_id", Integer, ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id",   Integer, ForeignKey("tags.id",   ondelete="CASCADE"), primary_key=True),
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Tag(Base):
    __tablename__ = "tags"

    id:    int       = Column(Integer, primary_key=True, autoincrement=True)
    name:  str       = Column(String, unique=True, nullable=False, index=True)
    color: str | None = Column(String, nullable=True, default=None)  # "#rrggbb" or None

    images: list["Image"] = relationship(
        "Image", secondary=image_tag_table, back_populates="tags"
    )
    groups: list["Group"] = relationship(
        "Group", secondary=group_tag_table, back_populates="tags"
    )


class Group(Base):
    __tablename__ = "groups"

    id:            int      = Column(Integer, primary_key=True, autoincrement=True)
    name:          str      = Column(String, nullable=False)
    cover_image_id: int | None = Column(Integer, ForeignKey("images.id", ondelete="SET NULL"), nullable=True)
    created_at:    datetime = Column(DateTime, default=datetime.utcnow)

    images:      list["Image"] = relationship("Image", back_populates="group", foreign_keys="Image.group_id")
    cover_image: "Image | None" = relationship("Image", foreign_keys=[cover_image_id])
    tags:        list[Tag]    = relationship("Tag", secondary=group_tag_table, back_populates="groups")


class Image(Base):
    __tablename__ = "images"

    id:         int            = Column(Integer, primary_key=True, autoincrement=True)
    path:       str            = Column(String, unique=True, nullable=False)
    group_id:   int | None     = Column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True)
    added_at:   datetime       = Column(DateTime, default=datetime.utcnow)
    ctime:      float | None   = Column(Float, nullable=True)

    group: Group | None = relationship("Group", back_populates="images", foreign_keys=[group_id])
    tags:  list[Tag]    = relationship("Tag", secondary=image_tag_table, back_populates="images")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def filename(self) -> str:
        return Path(self.path).name

    @property
    def exists(self) -> bool:
        return os.path.isfile(self.path)


# ---------------------------------------------------------------------------
# Engine / Session factory
# ---------------------------------------------------------------------------

_SessionLocal: sessionmaker | None = None


def init_db(db_path: str | Path) -> None:
    """Initialize the database engine and create tables."""
    global _SessionLocal
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    # マイグレーション: tags.color カラムが存在しない旧DBに追加
    with engine.connect() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tags)")]
        if "color" not in cols:
            conn.exec_driver_sql("ALTER TABLE tags ADD COLUMN color TEXT")
            conn.commit()
        img_cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(images)")]
        if "ctime" not in img_cols:
            conn.exec_driver_sql("ALTER TABLE images ADD COLUMN ctime REAL")
            conn.commit()
    # バックフィル: ctime が NULL の既存レコードにファイルシステムから取得して埋める
    _SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    _backfill_ctime()
    _normalize_backslash_paths()
    _migrate_group_image_tags()


def get_session() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()


def _normalize_backslash_paths() -> None:
    """path にバックスラッシュが含まれる既存レコードをフォワードスラッシュに変換する。"""
    from sqlalchemy import text
    with _SessionLocal() as session:
        rows = session.execute(
            text("SELECT id, path FROM images WHERE path LIKE '%\\\\%'")
        ).fetchall()
        if not rows:
            return
        updates = [
            {"id": row_id, "path": path.replace("\\", "/")}
            for row_id, path in rows
        ]
        session.execute(
            text("UPDATE images SET path = :path WHERE id = :id"),
            updates,
        )
        session.commit()


def _backfill_ctime() -> None:
    """ctime が NULL の既存レコードにファイルシステムから作成日時を一括設定する。"""
    from sqlalchemy import text
    with _SessionLocal() as session:
        rows = session.execute(
            text("SELECT id, path FROM images WHERE ctime IS NULL")
        ).fetchall()
        if not rows:
            return
        updates = []
        for row_id, path in rows:
            try:
                updates.append({"id": row_id, "ctime": os.path.getctime(path)})
            except OSError:
                updates.append({"id": row_id, "ctime": 0.0})
        session.execute(
            text("UPDATE images SET ctime = :ctime WHERE id = :id"),
            updates,
        )
        session.commit()


def _migrate_group_image_tags() -> None:
    """グループ内画像に残存するタグをグループ側に移行し、画像からは除去する。

    過去仕様の名残で image_tag にグループ所属画像のタグが残っている場合に
    一度だけ実行される起動時クリーンアップ。
    """
    from sqlalchemy import text
    with _SessionLocal() as session:
        # グループに所属し、かつタグを持つ画像のタグ情報を取得
        rows = session.execute(
            text(
                "SELECT it.image_id, i.group_id, it.tag_id "
                "FROM image_tag it "
                "JOIN images i ON i.id = it.image_id "
                "WHERE i.group_id IS NOT NULL"
            )
        ).fetchall()
        if not rows:
            return
        # group_tag に挿入（既存の組み合わせは無視）
        group_tag_pairs = list({(row[1], row[2]) for row in rows})
        session.execute(
            text("INSERT OR IGNORE INTO group_tag (group_id, tag_id) VALUES (:group_id, :tag_id)"),
            [{"group_id": gid, "tag_id": tid} for gid, tid in group_tag_pairs],
        )
        # image_tag から対象行を削除
        image_ids = list({row[0] for row in rows})
        session.execute(
            text(
                "DELETE FROM image_tag WHERE image_id IN ("
                + ",".join(str(iid) for iid in image_ids)
                + ")"
            )
        )
        session.commit()
