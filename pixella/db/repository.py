"""Repository layer — all DB interactions go through here."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from sqlalchemy import select, or_, delete
from sqlalchemy.orm import Session, selectinload

from .models import Image, Group, Tag, get_session


# ---------------------------------------------------------------------------
# Tag helpers
# ---------------------------------------------------------------------------

def get_or_create_tag(session: Session, name: str) -> Tag:
    name = name.strip().lower()
    tag = session.execute(select(Tag).where(Tag.name == name)).scalar_one_or_none()
    if tag is None:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    return tag


def all_tag_names(session: Session) -> list[str]:
    return [r[0] for r in session.execute(select(Tag.name).order_by(Tag.name)).all()]


def all_tag_color_map(session: Session) -> dict[str, str | None]:
    """タグ名 → カラー (#rrggbb | None) の辞書を返す。"""
    return {
        row[0]: row[1]
        for row in session.execute(select(Tag.name, Tag.color).order_by(Tag.name)).all()
    }


def set_tag_color(session: Session, tag_id: int, color: str | None) -> None:
    """タグのカラーを設定する。color は '#rrggbb' 形式か None。"""
    tag = session.execute(select(Tag).where(Tag.id == tag_id)).scalar_one_or_none()
    if tag:
        tag.color = color
        session.flush()


def all_tags_with_count(session: Session) -> list[tuple[Tag, int]]:
    """タグ一覧をそのタグが付いた画像数 + グループ数と合わせて返す。Tag オブジェクトに color が含まれる。"""
    from sqlalchemy import func
    from .models import image_tag_table, group_tag_table
    img_cnt = (
        select(func.count(image_tag_table.c.image_id))
        .where(image_tag_table.c.tag_id == Tag.id)
        .correlate(Tag)
        .scalar_subquery()
    )
    grp_cnt = (
        select(func.count(group_tag_table.c.group_id))
        .where(group_tag_table.c.tag_id == Tag.id)
        .correlate(Tag)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Tag, (img_cnt + grp_cnt).label("cnt"))
        .order_by(Tag.name)
    ).all()
    return [(row.Tag, row.cnt) for row in rows]


def delete_tag(session: Session, tag_id: int) -> None:
    """タグを削除する。image_tag / group_tag の紐付けは CASCADE で自動削除される。"""
    tag = session.execute(select(Tag).where(Tag.id == tag_id)).scalar_one_or_none()
    if tag:
        session.delete(tag)
        session.flush()


# ---------------------------------------------------------------------------
# Image CRUD
# ---------------------------------------------------------------------------

def add_image(path: str | Path) -> Image:
    with get_session() as session:
        existing = session.execute(
            select(Image).where(Image.path == str(path))
        ).scalar_one_or_none()
        if existing:
            return existing
        try:
            ct = os.path.getctime(str(path))
        except OSError:
            ct = 0.0
        img = Image(path=str(path), ctime=ct)
        session.add(img)
        session.commit()
        session.refresh(img)
        return img


def add_images(paths: list[str | Path]) -> tuple[int, int]:
    """画像を追加する。戻り値は (追加件数, スキップ件数) のタプル。"""
    added = 0
    skipped = 0
    with get_session() as session:
        for p in paths:
            existing = session.execute(
                select(Image).where(Image.path == str(p))
            ).scalar_one_or_none()
            if existing:
                skipped += 1
            else:
                try:
                    ct = os.path.getctime(str(p))
                except OSError:
                    ct = 0.0
                img = Image(path=str(p), ctime=ct)
                session.add(img)
                added += 1
        session.commit()
    return added, skipped


def get_image(image_id: int) -> Image | None:
    with get_session() as session:
        return session.get(Image, image_id)


def all_images(session: Session) -> list[Image]:
    return list(
        session.execute(
            select(Image).options(selectinload(Image.tags), selectinload(Image.group))
        ).scalars().all()
    )


def images_without_tags(session: Session) -> list[Image]:
    """タグが1つも付いていない、かつグループに所属していない画像一覧を返す。"""
    return list(
        session.execute(
            select(Image)
            .options(selectinload(Image.tags), selectinload(Image.group))
            .where(~Image.tags.any(), Image.group_id.is_(None))
        ).scalars().all()
    )


def groups_without_tags(session: Session) -> list[Group]:
    """タグが1つも付いていないグループ一覧を返す。"""
    return list(
        session.execute(
            select(Group)
            .options(
                selectinload(Group.images),
                selectinload(Group.tags),
                selectinload(Group.cover_image),
            )
            .where(~Group.tags.any())
        ).scalars().all()
    )


def set_image_tags(session: Session, image: Image, tag_names: list[str]) -> None:
    image.tags = [get_or_create_tag(session, n) for n in tag_names if n.strip()]
    session.flush()


def bulk_apply_tag_delta(
    image_ids: list[int],
    added: set[str],
    removed: set[str],
) -> None:
    """
    DBから画像をフレッシュロードし、ORMコレクション操作でタグを一括適用する。
    同一セッション内で全画像・全タグを扱うため identity map の競合なし。
    """
    if not image_ids or (not added and not removed):
        return
    with get_session() as session:
        # 全画像を同一セッションでフレッシュロード（identity map を共有）
        images = list(
            session.execute(
                select(Image)
                .where(Image.id.in_(image_ids))
                .options(selectinload(Image.tags))
            ).scalars()
        )

        # 追加タグ: 同セッションで get_or_create → identity map に登録済みの同一インスタンスを再利用
        add_tag_objs: list[Tag] = []
        for name in added:
            add_tag_objs.append(get_or_create_tag(session, name))
        if add_tag_objs:
            session.flush()  # 新規タグに ID を割り当てる

        # 削除タグ: DBから取得（存在するもののみ）
        rm_tag_objs: list[Tag] = []
        for name in removed:
            t = session.execute(
                select(Tag).where(Tag.name == name.lower())
            ).scalar_one_or_none()
            if t is not None:
                rm_tag_objs.append(t)

        for img in images:
            current_ids = {t.id for t in img.tags}
            for tag in add_tag_objs:
                if tag.id not in current_ids:
                    img.tags.append(tag)
                    current_ids.add(tag.id)
            for tag in rm_tag_objs:
                if tag in img.tags:
                    img.tags.remove(tag)

        session.commit()


def bulk_apply_group_tag_delta(
    group_ids: list[int],
    added: set[str],
    removed: set[str],
) -> None:
    """
    グループに対して bulk_apply_tag_delta と同等のデルタ操作を行う。
    """
    if not group_ids or (not added and not removed):
        return
    with get_session() as session:
        groups = list(
            session.execute(
                select(Group)
                .where(Group.id.in_(group_ids))
                .options(selectinload(Group.tags))
            ).scalars()
        )
        add_tag_objs: list[Tag] = []
        for name in added:
            add_tag_objs.append(get_or_create_tag(session, name))
        if add_tag_objs:
            session.flush()
        rm_tag_objs: list[Tag] = []
        for name in removed:
            t = session.execute(
                select(Tag).where(Tag.name == name.lower())
            ).scalar_one_or_none()
            if t is not None:
                rm_tag_objs.append(t)
        for grp in groups:
            current_ids = {t.id for t in grp.tags}
            for tag in add_tag_objs:
                if tag.id not in current_ids:
                    grp.tags.append(tag)
                    current_ids.add(tag.id)
            for tag in rm_tag_objs:
                if tag in grp.tags:
                    grp.tags.remove(tag)
        session.commit()


def remove_image(session: Session, image: Image) -> None:
    session.delete(image)
    session.flush()


# ---------------------------------------------------------------------------
# Group CRUD
# ---------------------------------------------------------------------------

def create_group(session: Session, name: str, image_ids: list[int]) -> Group:
    images = list(
        session.execute(
            select(Image)
            .where(Image.id.in_(image_ids))
            .options(selectinload(Image.tags))
        ).scalars().all()
    )
    group = Group(name=name)
    session.add(group)
    session.flush()  # get id

    # 画像のタグをマージしてグループに設定、画像からは外す
    seen_ids: set[int] = set()
    merged_tags: list = []
    for img in images:
        for tag in img.tags:
            if tag.id not in seen_ids:
                seen_ids.add(tag.id)
                merged_tags.append(tag)
        img.tags = []
        img.group_id = group.id
    group.tags = merged_tags

    if images:
        group.cover_image_id = images[0].id
    session.flush()
    return group


def rename_group(session: Session, group_id: int, new_name: str) -> None:
    """グループ名を変更する。"""
    group = session.get(Group, group_id)
    if group:
        group.name = new_name.strip()
        session.flush()


def dissolve_group(session: Session, group: Group) -> None:
    """グループを解除し、グループのタグを全メンバー画像に付与する。"""
    # 関連を確実にロード
    full_group = session.execute(
        select(Group).where(Group.id == group.id)
        .options(
            selectinload(Group.images).selectinload(Image.tags),
            selectinload(Group.tags),
        )
    ).scalar_one_or_none()
    if full_group is None:
        return

    group_tags = list(full_group.tags)
    for img in list(full_group.images):
        if group_tags:
            existing_ids = {t.id for t in img.tags}
            for tag in group_tags:
                if tag.id not in existing_ids:
                    img.tags.append(tag)
        img.group_id = None
    session.delete(full_group)
    session.flush()


def remove_image_from_group(session: Session, image: Image) -> None:
    if image.group is None:
        return
    group = image.group
    image.group_id = None
    session.flush()
    # if last image was removed, dissolve group
    remaining = session.execute(
        select(Image).where(Image.group_id == group.id)
    ).scalars().all()
    if not remaining:
        session.delete(group)
    elif group.cover_image_id == image.id:
        group.cover_image_id = remaining[0].id
    session.flush()


def set_group_cover(session: Session, group: Group, image: Image) -> None:
    group.cover_image_id = image.id
    session.flush()


def set_group_tags(session: Session, group: Group, tag_names: list[str]) -> None:
    group.tags = [get_or_create_tag(session, n) for n in tag_names if n.strip()]
    session.flush()


def all_groups(session: Session) -> list[Group]:
    return list(
        session.execute(
            select(Group).options(
                selectinload(Group.images),
                selectinload(Group.tags),
                selectinload(Group.cover_image),
            )
        ).scalars().all()
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_by_tags(
    session: Session,
    tag_names: list[str],
    mode: str = "and",
) -> tuple[list[Image], list[Group]]:
    """Return images and groups matching the specified tags.
    mode='and': all tags must be present (AND search)
    mode='or':  any tag must be present (OR search)
    """
    if not tag_names:
        return [], []

    # images
    img_q = select(Image).options(selectinload(Image.tags), selectinload(Image.group))
    if mode == "or":
        img_q = img_q.where(or_(*[Image.tags.any(Tag.name == n.lower()) for n in tag_names]))
    else:
        for name in tag_names:
            img_q = img_q.where(Image.tags.any(Tag.name == name.lower()))
    images = list(session.execute(img_q).scalars().all())

    # groups
    grp_q = select(Group).options(
        selectinload(Group.images),
        selectinload(Group.tags),
        selectinload(Group.cover_image),
    )
    if mode == "or":
        grp_q = grp_q.where(or_(*[Group.tags.any(Tag.name == n.lower()) for n in tag_names]))
    else:
        for name in tag_names:
            grp_q = grp_q.where(Group.tags.any(Tag.name == name.lower()))
    groups = list(session.execute(grp_q).scalars().all())

    return images, groups


# ---------------------------------------------------------------------------
# JSON export / import
# ---------------------------------------------------------------------------

def export_json(session: Session, path: str | Path) -> None:
    data = {
        "tags": [
            {"name": t.name, "color": t.color}
            for t in session.execute(select(Tag).order_by(Tag.name)).scalars().all()
        ],
        "images": [
            {
                "id": img.id,
                "path": img.path,
                "group_id": img.group_id,
                "ctime": img.ctime,
                "tags": [t.name for t in img.tags],
            }
            for img in all_images(session)
        ],
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "cover_image_id": g.cover_image_id,
                "tags": [t.name for t in g.tags],
                "image_ids": [img.id for img in g.images],
            }
            for g in all_groups(session)
        ],
    }
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_import_json(data: object) -> None:
    """JSONデータの基本バリデーション。問題があれば ValueError を raise する。"""
    if not isinstance(data, dict):
        raise ValueError("JSONのルートはオブジェクトである必要があります")
    for key in ("tags", "images", "groups"):
        if key not in data:
            raise ValueError(f"必須キー '{key}' がありません")
        if not isinstance(data[key], list):
            raise ValueError(f"'{key}' はリストである必要があります")
    for i, t in enumerate(data["tags"]):
        if not isinstance(t, dict) or "name" not in t:
            raise ValueError(f"tags[{i}]: 'name' フィールドが必要です")
        if not isinstance(t["name"], str):
            raise ValueError(f"tags[{i}]: 'name' は文字列である必要があります")
    for i, img in enumerate(data["images"]):
        if not isinstance(img, dict):
            raise ValueError(f"images[{i}]: オブジェクトである必要があります")
        if "id" not in img or "path" not in img:
            raise ValueError(f"images[{i}]: 'id' と 'path' フィールドが必要です")
        if not isinstance(img["path"], str):
            raise ValueError(f"images[{i}]: 'path' は文字列である必要があります")
    for i, grp in enumerate(data["groups"]):
        if not isinstance(grp, dict):
            raise ValueError(f"groups[{i}]: オブジェクトである必要があります")
        for field in ("id", "name", "image_ids"):
            if field not in grp:
                raise ValueError(f"groups[{i}]: '{field}' フィールドが必要です")
        if not isinstance(grp["image_ids"], list):
            raise ValueError(f"groups[{i}]: 'image_ids' はリストである必要があります")


def import_json(path: str | Path) -> None:
    """JSONファイルを読み込み、全データを置き換える。バリデーション後に実行する。"""
    from datetime import datetime as _dt
    from sqlalchemy import text

    raw = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSONのパースに失敗しました: {e}") from e

    _validate_import_json(data)

    with get_session() as session:
        # 全データを削除（CASCADE で中間テーブルも自動削除）
        session.execute(delete(Image))
        session.execute(delete(Group))
        session.execute(delete(Tag))
        session.flush()

        # タグを再作成
        tag_map: dict[str, Tag] = {}
        for t in data["tags"]:
            name = t["name"].strip().lower()
            if not name or name in tag_map:
                continue
            tag = Tag(name=name, color=t.get("color"))
            session.add(tag)
            tag_map[name] = tag
        session.flush()

        # 画像を再作成 (旧 id -> Image オブジェクト のマッピング)
        img_map: dict[int, Image] = {}
        for img_data in data["images"]:
            img = Image(
                path=img_data["path"],
                ctime=img_data.get("ctime"),
            )
            session.add(img)
            img_map[int(img_data["id"])] = img
        session.flush()

        # 画像-タグ関連付け
        for img_data in data["images"]:
            img = img_map[int(img_data["id"])]
            img.tags = [tag_map[n.strip().lower()] for n in img_data.get("tags", [])
                        if n.strip().lower() in tag_map]
        session.flush()

        # グループを再作成
        for grp_data in data["groups"]:
            grp = Group(name=grp_data["name"])
            session.add(grp)
            session.flush()

            member_imgs = [img_map[int(i)] for i in grp_data.get("image_ids", [])
                           if int(i) in img_map]
            for img in member_imgs:
                img.group_id = grp.id

            cover_old_id = grp_data.get("cover_image_id")
            if cover_old_id is not None and int(cover_old_id) in img_map:
                grp.cover_image_id = img_map[int(cover_old_id)].id
            elif member_imgs:
                grp.cover_image_id = member_imgs[0].id

            grp.tags = [tag_map[n.strip().lower()] for n in grp_data.get("tags", [])
                        if n.strip().lower() in tag_map]
            session.flush()

        session.commit()
