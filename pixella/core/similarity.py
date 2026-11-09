"""類似画像検出 — perceptual hash による候補絞り込みと、ピクセル照合・構造照合によるクラスタリング。

Pillow のみに依存する純関数モジュール (Qt 非依存)。
ハッシュは 64bit 整数として扱い、DB には 16 桁 hex 文字列で保存する。
ピクセル照合用の縮小グレースケール (32x32 生画素) は bytes として扱い BLOB で保存する。

判定は 候補列挙 (dHash バンド) → ハッシュゲート → ピクセル照合 → 構造照合 の順に行い、
類似ペアをセット間密度つきでクラスタリングする。構造照合と密度条件は、白背景が大半を占める
別イラスト同士の誤検出と、誤ペアを介した数珠つなぎの巨大セット化を防ぐためのもの。
構造照合は 32x32 生画素から都度計算するため、DB に追加データは不要。
"""
from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from PIL import Image as PILImage
from PIL import ImageChops

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
# ピクセル照合 (第 3 段)
# ---------------------------------------------------------------------------

def normalize_gray(gray: bytes) -> bytes:
    """明度・コントラストを正規化する (平均 128, 標準偏差 48 に線形リマップ)。

    明るさ・コントラスト補正だけが違う同一画像を同じ表現に揃える。
    標準偏差の下限 8.0 は、平坦な画像でノイズを過剰増幅しないためのガード。
    """
    n = len(gray)
    m = sum(gray) / n
    var = sum(c * (v - m) * (v - m) for v, c in Counter(gray).items()) / n
    k = 48.0 / max(math.sqrt(var), 8.0)
    # 画素値ごとの変換表を作って一括変換する (画素ごとの Python ループを避ける)
    return gray.translate(bytes(min(255, max(0, round((v - m) * k + 128))) for v in range(256)))


def _gray_image(data: bytes) -> PILImage.Image:
    return PILImage.frombytes("L", (GRAY_SIZE, GRAY_SIZE), data)


def _trimmed_mean(hist: list[int], trim_ratio: float) -> float:
    """絶対差ヒストグラム (256 ビン) から、差分上位 trim_ratio の画素を除いた平均を返す。"""
    total_px = sum(hist)
    keep = total_px - int(total_px * trim_ratio)
    total = count = 0
    for v in range(256):
        c = hist[v]
        if count + c >= keep:
            total += v * (keep - count)
            break
        total += v * c
        count += c
    return total / keep


def norm_difference(na: bytes, nb: bytes) -> PILImage.Image:
    """正規化済みグレースケール同士の絶対差画像を返す。"""
    return ImageChops.difference(_gray_image(na), _gray_image(nb))


def pixel_score(na: bytes, nb: bytes, trim_ratio: float) -> float:
    """正規化済みグレースケール同士の trimmed mean 絶対差 (0〜255) を返す。

    差分の大きい上位 trim_ratio の画素を除外して平均する。
    局所差分 (文字入れ・透かし) は trim で無視され、全体的な違いだけがスコアに残る。
    差分ヒストグラムは Pillow の画像演算 (C 実装) で求める。
    """
    return _trimmed_mean(norm_difference(na, nb).histogram(), trim_ratio)


# ---------------------------------------------------------------------------
# 構造照合 (第 4 段)
# ---------------------------------------------------------------------------
# ピクセル照合は「画素の 75% がほぼ一致」のような割合で判定するため、白背景が画像の
# 大半を占めるイラスト同士 (立ち絵・ちびキャラ) は、描かれた内容が別物でも背景の一致
# だけで通過してしまう。構造照合は背景の一致を証拠に数えず、次の 2 指標で同一性を確かめる。
#
#   エッジ一致率 : どちらかの画像で強いエッジ (Sobel 勾配) がある画素のうち、
#                  もう一方にも同じ向き・同程度の強さのエッジがある割合
#   内容一致率   : どちらかの画像で背景 (外周画素の中央値) と異なる画素のうち、
#                  画素値が一致する割合 (生画素と正規化後の差の小さい方で比較)
#
# 差分画像 (表情・文字入れ・小物の追加) は変更部以外の輪郭が一致するためエッジ一致率が
# 高く、別イラストは輪郭の位置が合わないため低い。
# 構造スコア = 0.7 x エッジ一致率 + 0.3 x 内容一致率 を Preset.struct_min と比較する。
# しきい値は実ライブラリの目視ラベル付きペアで校正した (再調整は tools/verify_similarity.py)。

STRUCT_EDGE_WEIGHT = 0.7

# エッジは Sobel 勾配 (1/4 スケール) の向きを 256 段階 (1 周)、強さを対数で量子化し 1 画素 1 バイトに格納する。
# 向きの差 32 (=45 度) 以内かつ強さの比 2^-1.75 (約 0.3) 以上を「一致」とする。
# 両条件の許容幅を同じ 32 に揃えておくと max(向き差, 強さ差) <= 32 の 1 回の比較で済む。
_EDGE_TOL = 32
_EDGE_LEV_PER_OCTAVE = _EDGE_TOL / 1.75
_EDGE_STRONG_LEV = 80        # 量子化強さ 80 (勾配強度 約 20) 以上の画素を「強いエッジ」とする
EDGE_MIN_PIXELS = 16         # 強いエッジ画素がこれ未満のペアは平坦画像とみなし構造照合を適用しない

# 内容画素: 外周画素の中央値 (=背景色) から _CONTENT_BG_TOL を超えて離れた画素
_CONTENT_BG_TOL = 8
_CONTENT_MIN_PIXELS = 32     # 内容画素がこれ未満なら全画素で比較する
_CONTENT_MATCH_TOL = 16

_STRONG_FLOOR = PILImage.new("L", (GRAY_SIZE, GRAY_SIZE), _EDGE_STRONG_LEV - 1)


def _border_median(gray: bytes) -> float:
    n = GRAY_SIZE
    ring = list(gray[:n]) + list(gray[n * (n - 1):])
    for y in range(1, n - 1):
        ring.append(gray[y * n])
        ring.append(gray[y * n + n - 1])
    return statistics.median(ring)


def structure_planes(gray: bytes) -> tuple[bytes, bytes, bytes]:
    """構造照合用の画素プレーン (内容マスク, エッジ向き, エッジ強さ) を返す。

    1 画像につき 1 回だけ計算し、ペア比較は Pillow の画像演算 (C 実装) で行う。
    """
    n = GRAY_SIZE
    bg = _border_median(gray)
    content = bytes(255 if abs(p - bg) > _CONTENT_BG_TOL else 0 for p in gray)

    direction = bytearray(GRAY_PIXELS)
    level = bytearray(GRAY_PIXELS)
    rows = [gray[y * n:(y + 1) * n] for y in range(n)]
    atan2, hypot, log2 = math.atan2, math.hypot, math.log2
    turn = 256 / (2 * math.pi)
    for y in range(n):
        up = rows[y - 1 if y else 0]
        mid = rows[y]
        down = rows[y + 1 if y < n - 1 else n - 1]
        base = y * n
        for x in range(n):
            xl = x - 1 if x else 0
            xr = x + 1 if x < n - 1 else n - 1
            gx = (up[xr] + 2 * mid[xr] + down[xr]) - (up[xl] + 2 * mid[xl] + down[xl])
            gy = (down[xl] + 2 * down[x] + down[xr]) - (up[xl] + 2 * up[x] + up[xr])
            mag = hypot(gx, gy) * 0.25
            if mag < 1.0:
                continue
            direction[base + x] = round(atan2(gy, gx) * turn) & 0xFF
            level[base + x] = min(255, round(log2(mag) * _EDGE_LEV_PER_OCTAVE))
    return content, bytes(direction), bytes(level)


def structure_score(
    gray_a: bytes, planes_a: tuple[bytes, bytes, bytes],
    gray_b: bytes, planes_b: tuple[bytes, bytes, bytes],
    norm_diff: PILImage.Image,
) -> float | None:
    """構造スコア (0〜1、高いほど同じ絵) を返す。平坦な画像同士で判定材料がなければ None。

    planes_* は structure_planes() の戻り値、norm_diff は正規化 gray 同士の差分画像
    (ピクセル照合で求めたものを再利用する。単体で使う場合は norm_difference() で求める)。
    """
    content_a, dir_a, lev_a = planes_a
    content_b, dir_b, lev_b = planes_b

    # エッジ一致率: どちらかが強いエッジの画素 (strong が非 0) の中で向き・強さが揃う割合
    la, lb = _gray_image(lev_a), _gray_image(lev_b)
    strong = ImageChops.subtract(ImageChops.lighter(la, lb), _STRONG_FLOOR)
    da, db = _gray_image(dir_a), _gray_image(dir_b)
    angle = ImageChops.darker(ImageChops.subtract_modulo(da, db), ImageChops.subtract_modulo(db, da))
    mismatch = ImageChops.lighter(angle, ImageChops.difference(la, lb))
    hist = mismatch.histogram(strong)
    edges = sum(hist)
    if edges < EDGE_MIN_PIXELS:
        return None
    edge_ratio = sum(hist[:_EDGE_TOL + 1]) / edges

    # 内容一致率 (明度補正違いにも対応するため生画素と正規化後の差の小さい方を使う)
    diff = ImageChops.darker(norm_diff, ImageChops.difference(_gray_image(gray_a), _gray_image(gray_b)))
    hist = diff.histogram(ImageChops.lighter(_gray_image(content_a), _gray_image(content_b)))
    pixels = sum(hist)
    if pixels < _CONTENT_MIN_PIXELS:
        hist = diff.histogram()
        pixels = GRAY_PIXELS
    content_ratio = sum(hist[:_CONTENT_MATCH_TOL + 1]) / pixels

    return STRUCT_EDGE_WEIGHT * edge_ratio + (1 - STRUCT_EDGE_WEIGHT) * content_ratio


# ---------------------------------------------------------------------------
# プリセット
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Preset:
    """類似判定条件: 共通ハッシュゲート (GATE_DHASH_MAX / GATE_PHASH_MAX) を
    通過した候補ペアのうち、ピクセルスコア <= pixel_max かつ構造スコア >= struct_min の
    ものを類似とみなす。

    ハッシュは候補絞り込み (recall) 専用で、精度はピクセル照合と構造照合が担う。
    trim_ratio を上げるほど局所差分 (文字入れ・透かし) を許容する。
    struct_min を下げるほど大きな差分 (衣装・配色の差し替え) を許容する。
    """
    key: str
    label: str
    description: str
    trim_ratio: float   # 差分上位この割合の画素を除外 (局所差分の許容量)
    pixel_max: float    # trimmed mean 絶対差のしきい値 (0〜255)
    struct_min: float   # 構造スコアの下限 (0〜1)


PRESETS: list[Preset] = [
    Preset(
        key="strict",
        label="厳密",
        description="ほぼ同一の画像のみ (解像度違い・再圧縮・明るさ補正)",
        trim_ratio=0.05,
        pixel_max=6.0,
        struct_min=0.50,
    ),
    Preset(
        key="standard",
        label="標準",
        description="文字入れ・透かし・表情差分など一部だけ異なる画像も含める",
        trim_ratio=0.25,
        pixel_max=10.0,
        struct_min=0.30,
    ),
    Preset(
        key="loose",
        label="ゆるめ",
        description="衣装・配色の差し替えなど差分が大きめの画像も含める (誤検出も増える)",
        trim_ratio=0.35,
        pixel_max=16.0,
        struct_min=0.20,
    ),
]


# ---------------------------------------------------------------------------
# クラスタリング
# ---------------------------------------------------------------------------

# 2 つのセットを統合するには、セット間の全組み合わせのうちこの割合以上が類似ペアである必要がある。
# 単純な連結成分 (A≒B かつ B≒C なら A,B,C を 1 セット) だと、少数の誤ペアを介して
# 無関係な画像が数珠つなぎに数千枚規模の 1 セットへまとまってしまう。
LINK_DENSITY_MIN = 0.5


def _band_widths(bands: int) -> list[int]:
    """64bit を bands 個にできるだけ均等に分割した幅のリスト。"""
    base, rem = divmod(HASH_BITS, bands)
    return [base + (1 if i < rem else 0) for i in range(bands)]


def _cluster_pairs(pairs: list[tuple[float, int, int]]) -> list[list[int]]:
    """類似ペア (強さ, a, b) をセットにまとめる。

    1) 強いペアから順に、2 セット間の類似ペア数が |X| x |Y| x LINK_DENSITY_MIN 以上のときだけ統合する
    2) 1) でどのセットにも入らなかった画像は、最も強いペア相手のセットに追加する。
       この画像のペア相手は、そのペアを処理した時点で既に 2 枚以上のセットに属していた
       (両方が単独なら必ず統合される) ため、追加先は必ず存在する。追加はセット同士の
       統合ではないので連鎖は起きず、類似ペアを持つ画像は必ずいずれかのセットに入る。
    """
    adj: dict[int, set[int]] = defaultdict(set)
    for _s, a, b in pairs:
        adj[a].add(b)
        adj[b].add(a)
    owner: dict[int, int] = {}
    members: dict[int, list[int]] = {}
    for _s, a, b in sorted(pairs, key=lambda p: p[0], reverse=True):
        x, y = owner.get(a, a), owner.get(b, b)
        if x == y:
            continue
        mx, my = members.get(x, [x]), members.get(y, [y])
        if len(mx) > len(my):
            x, y, mx, my = y, x, my, mx
        if len(mx) * len(my) > 1:
            yset = set(my)
            cross = sum(len(adj[m] & yset) for m in mx)
            if cross < LINK_DENSITY_MIN * len(mx) * len(my):
                continue
        owner[y] = y
        for m in mx:
            owner[m] = y
        members[y] = my + mx
        members.pop(x, None)

    best: dict[int, tuple[float, int]] = {}
    for s, a, b in pairs:
        for u, v in ((a, b), (b, a)):
            if u not in owner and (u not in best or s > best[u][0]):
                best[u] = (s, owner[v])
    for u, (_s, root) in best.items():
        members[root].append(u)
    return list(members.values())


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
    候補ペアはハッシュゲート → ピクセル照合 → 構造照合の 3 段で検証し、
    セット間密度を条件にクラスタリングする (_cluster_pairs)。
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

    # 3) 候補ペアをハッシュゲート → ピクセル照合 → 構造照合で検証する
    norm: list[PILImage.Image | None] = [None] * n                # 正規化 gray (画像化済み) の遅延キャッシュ
    planes: list[tuple[bytes, bytes, bytes] | None] = [None] * n   # 構造照合プレーンの遅延キャッシュ
    seen: set[tuple[int, int]] = set()      # ゲート通過ペアのみ記録 (メモリ節約)
    similar: list[tuple[float, int, int]] = []
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
                pa, da, ga = reps[pair[0]]
                pb, db, gb = reps[pair[1]]
                # hamming() の呼び出しコストが効くほど回るループなのでインライン展開している
                if (da ^ db).bit_count() > GATE_DHASH_MAX:
                    continue
                if (pa ^ pb).bit_count() > GATE_PHASH_MAX:
                    continue
                if pair in seen:
                    continue
                seen.add(pair)
                a, b = pair
                if norm[a] is None:
                    norm[a] = _gray_image(normalize_gray(ga))
                if norm[b] is None:
                    norm[b] = _gray_image(normalize_gray(gb))
                diff = ImageChops.difference(norm[a], norm[b])
                if _trimmed_mean(diff.histogram(), preset.trim_ratio) > preset.pixel_max:
                    continue
                if planes[a] is None:
                    planes[a] = structure_planes(ga)
                if planes[b] is None:
                    planes[b] = structure_planes(gb)
                score = structure_score(ga, planes[a], gb, planes[b], diff)
                if score is None:
                    # 平坦画像同士: 構造の判定材料がないのでピクセル照合の結果に従う
                    score = 1.0
                elif score < preset.struct_min:
                    continue
                similar.append((score, a, b))

    # 4) セット間密度を条件にクラスタリングし、image_id に展開する
    clusters = [
        [image_id for idx in idxs for image_id in exact[reps[idx]]]
        for idxs in _cluster_pairs(similar)
    ]
    # 他の画像と類似ペアを持たない完全一致グループもセットとして返す
    paired = {idx for _s, a, b in similar for idx in (a, b)}
    clusters.extend(
        ids for idx, key in enumerate(reps)
        if idx not in paired and len(ids := exact[key]) >= 2
    )
    clusters.sort(key=len, reverse=True)
    return clusters
