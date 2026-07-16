"""類似画像検出 — perceptual hash (dHash / pHash) の計算とクラスタリング。

Pillow のみに依存する純関数モジュール (Qt 非依存)。
ハッシュは 64bit 整数として扱い、DB には 16 桁 hex 文字列で保存する。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from PIL import Image as PILImage

HASH_BITS = 64

# pHash 用: 32x32 の 2D DCT-II のうち左上 8x8 だけを部分計算するための
# DCT 基底の先頭 8 行 (8x32)。モジュールロード時に事前計算する。
_DCT_N = 32
_DCT_C8 = [
    [math.cos(math.pi * u * (2 * x + 1) / (2 * _DCT_N)) for x in range(_DCT_N)]
    for u in range(8)
]


# ---------------------------------------------------------------------------
# ハッシュ計算
# ---------------------------------------------------------------------------

def dhash64(img: PILImage.Image) -> int:
    """dHash: 9x8 に縮小し、各行で隣接画素の大小を 64bit にする。"""
    small = img.convert("L").resize((9, 8), PILImage.Resampling.LANCZOS)
    px = list(small.getdata())
    bits = 0
    for y in range(8):
        row = y * 9
        for x in range(8):
            bits = (bits << 1) | (1 if px[row + x] < px[row + x + 1] else 0)
    return bits


def phash64(img: PILImage.Image) -> int:
    """pHash: 32x32 グレースケールの DCT 低周波 8x8 を中央値と比較して 64bit にする。"""
    small = img.convert("L").resize((_DCT_N, _DCT_N), PILImage.Resampling.LANCZOS)
    px = list(small.getdata())
    rows = [px[i * _DCT_N:(i + 1) * _DCT_N] for i in range(_DCT_N)]
    # Z = C8 · X · C8ᵀ (左上 8x8 のみ計算。フル 32x32 DCT は行わない)
    mid = [
        [sum(_DCT_C8[u][i] * rows[i][j] for i in range(_DCT_N)) for j in range(_DCT_N)]
        for u in range(8)
    ]
    coeffs = [
        sum(mid[u][j] * _DCT_C8[v][j] for j in range(_DCT_N))
        for u in range(8)
        for v in range(8)
    ]
    med = statistics.median(coeffs)
    bits = 0
    for c in coeffs:
        bits = (bits << 1) | (1 if c > med else 0)
    return bits


def compute_image_hashes(image_path: str) -> tuple[int, int, int, int]:
    """画像ファイルを 1 回だけ開き (phash, dhash, width, height) を返す。

    失敗時は例外を送出する (呼び出し側でハンドリングする)。
    """
    with PILImage.open(image_path) as img:
        # アニメーション GIF/WebP は先頭フレームを使う
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)
        width, height = img.size
        # JPEG はデコード段階で縮小して高速化 (他形式では no-op)
        img.draft("L", (_DCT_N * 4, _DCT_N * 4))
        return phash64(img), dhash64(img), width, height


def hash_to_hex(h: int) -> str:
    return f"{h:016x}"


def hex_to_hash(s: str) -> int:
    return int(s, 16)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# ---------------------------------------------------------------------------
# プリセット
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """類似判定条件: dHash 距離 <= dhash_max かつ pHash 距離 <= phash_max。

    実測では局所的な差分 (透かし・一部塗り潰し) は pHash を大きく崩す一方
    dHash にはほとんど影響しないため、dHash を主判定に使う。
    pHash は「構図がまったく別の画像」(例: 別文面のスクリーンショット同士は
    dHash が偶然近くなる) を弾くための上限キャップとして働く。
    """
    key: str
    label: str
    description: str
    dhash_max: int   # dHash ハミング距離のしきい値 (主判定・バンド分割の基盤)
    phash_max: int   # pHash 距離の上限キャップ (別内容の誤検出を除外)


PRESETS: list[Preset] = [
    Preset(
        key="strict",
        label="厳密",
        description="ほぼ同一の画像のみ (完全一致・解像度違い・再圧縮)",
        dhash_max=2,
        phash_max=4,
    ),
    Preset(
        key="standard",
        label="標準",
        description="一部だけ異なる画像も含める",
        dhash_max=8,
        phash_max=20,
    ),
    Preset(
        key="loose",
        label="ゆるめ",
        description="差分が大きめの画像も含める",
        dhash_max=12,
        phash_max=28,
    ),
]


# ---------------------------------------------------------------------------
# クラスタリング
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))
        self._rank = [0] * n

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:  # 経路圧縮
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        self._parent[rb] = ra
        if self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1


def _band_widths(bands: int) -> list[int]:
    """64bit を bands 個にできるだけ均等に分割した幅のリスト。"""
    base, rem = divmod(HASH_BITS, bands)
    return [base + (1 if i < rem else 0) for i in range(bands)]


def find_similar_clusters(
    entries: list[tuple[int, int, int]],
    preset: Preset,
) -> list[list[int]]:
    """(image_id, phash, dhash) のリストから類似セットを返す。

    pigeonhole 原理: 主判定の dHash を「しきい値 d + 1」個のバンドに分割すると、
    距離 <= d のペアは必ずどれか 1 バンドが完全一致するため、
    同一バケットに入ったペアだけを候補として検証すればよい。
    戻り値は image_id のリストのリスト (サイズ 2 以上、枚数降順)。
    """
    # 1) (phash, dhash) 完全一致を事前縮約し、代表ハッシュのみ距離計算する
    exact: dict[tuple[int, int], list[int]] = {}
    for image_id, ph, dh in entries:
        exact.setdefault((ph, dh), []).append(image_id)
    reps = list(exact.keys())
    n = len(reps)

    # 2) dHash のバンド分割で候補ペアを列挙
    bands = preset.dhash_max + 1
    widths = _band_widths(bands)
    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_ph, dh) in enumerate(reps):
        shift = HASH_BITS
        for band_idx, width in enumerate(widths):
            shift -= width
            value = (dh >> shift) & ((1 << width) - 1)
            buckets.setdefault((band_idx, value), []).append(idx)

    # 3) 候補ペアをフルハミング距離で検証し Union-Find でマージ
    uf = _UnionFind(n)
    seen: set[tuple[int, int]] = set()
    for members in buckets.values():
        if len(members) < 2:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = (members[i], members[j])
                if pair in seen:
                    continue
                seen.add(pair)
                pa, da = reps[pair[0]]
                pb, db = reps[pair[1]]
                if hamming(da, db) > preset.dhash_max:
                    continue
                if hamming(pa, pb) > preset.phash_max:
                    continue
                uf.union(pair[0], pair[1])

    # 4) 連結成分ごとに image_id を展開し、サイズ 2 以上を枚数降順で返す
    components: dict[int, list[int]] = {}
    for idx, key in enumerate(reps):
        components.setdefault(uf.find(idx), []).extend(exact[key])
    clusters = [ids for ids in components.values() if len(ids) >= 2]
    clusters.sort(key=len, reverse=True)
    return clusters
