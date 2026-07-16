"""類似画像検出 — perceptual hash による候補絞り込みとピクセル照合クラスタリング。

Pillow のみに依存する純関数モジュール (Qt 非依存)。
ハッシュは 64bit 整数として扱い、DB には 16 桁 hex 文字列で保存する。
ピクセル照合用の縮小グレースケール (32x32 生画素) は bytes として扱い BLOB で保存する。
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

# ピクセル照合用の縮小グレースケールのサイズ (_DCT_N と同値だが役割が違うので別名)
GRAY_SIZE = 32
GRAY_PIXELS = GRAY_SIZE * GRAY_SIZE

# 候補絞り込み用ハッシュゲート (全プリセット共通)。
# recall 担保のため緩めにし、最終判定 (precision) はピクセル照合が担う。
GATE_DHASH_MAX = 16   # 局所差分 (帯状の文字入れ等) は dHash の 2〜3 行 (16〜24bit) を壊しうる
GATE_PHASH_MAX = 26   # 無関係ペアの距離分布 (平均 32, σ≈4) の大半を前段で捨てる

# 均等バンド分割で「確実に」候補列挙する dHash 距離 (pigeonhole 保証の上限)。
# これを超え GATE_DHASH_MAX 以下のペアは行単位バンド (局所差分で無傷の行が残る性質) で拾う。
_ENUM_EXACT_MAX = 10


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
    return _phash64_from_pixels(list(small.getdata()))


def _phash64_from_pixels(px: list[int]) -> int:
    """32x32 グレースケール画素列から pHash を計算する。"""
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


def compute_image_hashes(image_path: str) -> tuple[int, int, bytes, int, int]:
    """画像ファイルを 1 回だけ開き (phash, dhash, gray, width, height) を返す。

    gray はピクセル照合用の 32x32 グレースケール生画素 (正規化前)。
    pHash 用の 32x32 リサイズ結果を共有するため追加コストはほぼない。
    失敗時は例外を送出する (呼び出し側でハンドリングする)。
    """
    with PILImage.open(image_path) as img:
        # アニメーション GIF/WebP は先頭フレームを使う
        if hasattr(img, "n_frames") and img.n_frames > 1:
            img.seek(0)
        width, height = img.size
        # JPEG はデコード段階で縮小して高速化 (他形式では no-op)
        img.draft("L", (_DCT_N * 4, _DCT_N * 4))
        small = img.convert("L").resize((GRAY_SIZE, GRAY_SIZE), PILImage.Resampling.LANCZOS)
        px = list(small.getdata())
        return _phash64_from_pixels(px), dhash64(img), bytes(px), width, height


def hash_to_hex(h: int) -> str:
    return f"{h:016x}"


def hex_to_hash(s: str) -> int:
    return int(s, 16)


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# ---------------------------------------------------------------------------
# ピクセル照合
# ---------------------------------------------------------------------------

def normalize_gray(gray: bytes) -> bytes:
    """明度・コントラストを正規化する (平均 128, 標準偏差 48 に線形リマップ)。

    明るさ・コントラスト補正だけが違う同一画像を同じ表現に揃える。
    標準偏差の下限 8.0 は、平坦な画像でノイズを過剰増幅しないためのガード。
    """
    n = len(gray)
    m = sum(gray) / n
    var = sum((p - m) * (p - m) for p in gray) / n
    k = 48.0 / max(math.sqrt(var), 8.0)
    return bytes(min(255, max(0, round((p - m) * k + 128))) for p in gray)


def pixel_score(na: bytes, nb: bytes, trim_ratio: float) -> float:
    """正規化済みグレースケール同士の trimmed mean 絶対差 (0〜255) を返す。

    差分の大きい上位 trim_ratio の画素を除外して平均する。
    局所差分 (文字入れ・透かし) は trim で無視され、全体的な違いだけがスコアに残る。
    ソート (O(n log n)) の代わりに 256 ビンのヒストグラムで求める。
    """
    hist = [0] * 256
    for a, b in zip(na, nb):
        hist[a - b if a >= b else b - a] += 1
    keep = len(na) - int(len(na) * trim_ratio)
    total = count = 0
    for v in range(256):
        c = hist[v]
        if count + c >= keep:
            total += v * (keep - count)
            break
        total += v * c
        count += c
    return total / keep


# ---------------------------------------------------------------------------
# プリセット
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """類似判定条件: 共通ハッシュゲート (GATE_DHASH_MAX / GATE_PHASH_MAX) を
    通過した候補ペアのうち、ピクセルスコア <= pixel_max のものを類似とみなす。

    ハッシュは候補絞り込み (recall) 専用で、精度はピクセル照合が担う。
    trim_ratio を上げるほど局所差分 (文字入れ・透かし) を許容する。
    """
    key: str
    label: str
    description: str
    trim_ratio: float   # 差分上位この割合の画素を除外 (局所差分の許容量)
    pixel_max: float    # trimmed mean 絶対差のしきい値 (0〜255)


PRESETS: list[Preset] = [
    Preset(
        key="strict",
        label="厳密",
        description="ほぼ同一の画像のみ (解像度違い・再圧縮・明るさ補正)",
        trim_ratio=0.05,
        pixel_max=6.0,
    ),
    Preset(
        key="standard",
        label="標準",
        description="文字入れ・透かしなど一部だけ異なる画像も含める",
        trim_ratio=0.25,
        pixel_max=10.0,
    ),
    Preset(
        key="loose",
        label="ゆるめ",
        description="差分が大きめの画像も含める (誤検出も増える)",
        trim_ratio=0.35,
        pixel_max=16.0,
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
    entries: list[tuple[int, int, int, bytes]],
    preset: Preset,
) -> list[list[int]]:
    """(image_id, phash, dhash, gray) のリストから類似セットを返す。

    候補列挙は 2 種類のバンド分割の和集合で行う:
    - 均等バンド: pigeonhole 原理により、dHash を「しきい値 d + 1」個に分割すると
      距離 <= d (= _ENUM_EXACT_MAX) のペアは必ずどれか 1 バンドが完全一致する
    - 行単位バンド (8bit x 8): 文字入れ・透かし等の局所差分は dHash の特定の行だけを
      壊すため、距離が _ENUM_EXACT_MAX を超えても無傷の行の一致で候補に挙がる
    候補ペアはハッシュゲート → ピクセル照合の 2 段で検証する。
    戻り値は image_id のリストのリスト (サイズ 2 以上、枚数降順)。
    """
    # 1) (phash, dhash, gray) 完全一致を事前縮約し、代表のみ距離計算する
    exact: dict[tuple[int, int, bytes], list[int]] = {}
    for image_id, ph, dh, gray in entries:
        exact.setdefault((ph, dh, gray), []).append(image_id)
    reps = list(exact.keys())
    n = len(reps)

    # 2) dHash のバンド分割で候補ペアを列挙 (均等バンド + 行単位バンド)
    bands = _ENUM_EXACT_MAX + 1
    widths = _band_widths(bands)
    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_ph, dh, _gray) in enumerate(reps):
        shift = HASH_BITS
        for band_idx, width in enumerate(widths):
            shift -= width
            value = (dh >> shift) & ((1 << width) - 1)
            buckets.setdefault((band_idx, value), []).append(idx)
        for row in range(8):  # dHash は行単位 8bit で構成される
            value = (dh >> (row * 8)) & 0xFF
            buckets.setdefault((bands + row, value), []).append(idx)

    # 3) 候補ペアをハッシュゲート → ピクセル照合で検証し Union-Find でマージ
    uf = _UnionFind(n)
    norm: list[bytes | None] = [None] * n   # 正規化 gray の遅延キャッシュ
    seen: set[tuple[int, int]] = set()      # ゲート通過ペアのみ記録 (メモリ節約)
    # 平坦な行 (空・白背景等) は多数の画像で同じ行バンド値になり識別力がないため、
    # 巨大化した行バンドバケットは飛ばす (dHash 距離 <= _ENUM_EXACT_MAX は均等バンドが保証する)
    row_cap = max(64, n // 32)
    for (band_idx, _value), members in buckets.items():
        if len(members) < 2:
            continue
        if band_idx >= bands and len(members) > row_cap:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = (members[i], members[j])
                pa, da, _ = reps[pair[0]]
                pb, db, _ = reps[pair[1]]
                if hamming(da, db) > GATE_DHASH_MAX:
                    continue
                if hamming(pa, pb) > GATE_PHASH_MAX:
                    continue
                if pair in seen:
                    continue
                seen.add(pair)
                a, b = pair
                if uf.find(a) == uf.find(b):  # 既に同一クラスタなら照合を省く
                    continue
                if norm[a] is None:
                    norm[a] = normalize_gray(reps[a][2])
                if norm[b] is None:
                    norm[b] = normalize_gray(reps[b][2])
                if pixel_score(norm[a], norm[b], preset.trim_ratio) > preset.pixel_max:
                    continue
                uf.union(a, b)

    # 4) 連結成分ごとに image_id を展開し、サイズ 2 以上を枚数降順で返す
    components: dict[int, list[int]] = {}
    for idx, key in enumerate(reps):
        components.setdefault(uf.find(idx), []).extend(exact[key])
    clusters = [ids for ids in components.values() if len(ids) >= 2]
    clusters.sort(key=len, reverse=True)
    return clusters
