"""類似画像検出の検証・キャリブレーションスクリプト (Qt 非依存)。

元画像から変種 (リサイズ・再圧縮・明度変更・文字入れ・部分差分等) と無関係画像を生成し、
ハッシュゲートの recall、ピクセルスコア・構造スコアの分離度を実測する。
PRESETS のしきい値調整の根拠データを出力し、end-to-end の分類テストを行う。

元画像は「全面に絵がある画像」と「白背景に小さくキャラが描かれた画像」の 2 種類。
後者は背景の一致だけでピクセル照合を素通りしやすい誤検出の典型で、構造照合の回帰テストを兼ねる。

使い方 (pixella/ ディレクトリから):
    python tools\verify_similarity.py            # 手続き生成画像で検証
    python tools\verify_similarity.py <画像DIR>  # 実画像を元画像として検証
    python tools\verify_similarity.py --bench    # 10,000 件の性能ベンチのみ
"""
from __future__ import annotations

import math
import random
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows コンソール (cp932) でも UTF-8 で出力する
sys.stdout.reconfigure(encoding="utf-8")

from pixella.core.similarity import (  # noqa: E402
    GATE_DHASH_MAX, GATE_PHASH_MAX, PRESETS,
    compute_image_hashes, find_similar_clusters, hamming,
    norm_difference, normalize_gray, pixel_score, structure_planes, structure_score,
)

BASE_COUNT = 6          # 手続き生成する元画像 (全面) の数
SPRITE_COUNT = 6        # 手続き生成する元画像 (白背景キャラ) の数
BASE_SIZE = (512, 384)

# 変種名 → (この変種を検出すべき最小プリセットの index)。strict=0, standard=1, loose=2
VARIANTS_EXPECT: dict[str, int] = {
    "resize50": 0,
    "resize33": 0,
    "jpeg40": 0,
    "jpeg80": 0,
    "bright+20": 0,
    "bright-20": 0,
    "contrast08": 0,
    "text10": 1,
    "patch05": 1,
    "text25": 2,
}


# ---------------------------------------------------------------------------
# テスト画像生成
# ---------------------------------------------------------------------------

def make_base(seed: int, size: tuple[int, int] = BASE_SIZE) -> Image.Image:
    """グラデーション + 図形 + ノイズの手続き生成画像 (seed ごとに別内容)。"""
    rnd = random.Random(seed)
    grad = Image.linear_gradient("L").rotate(rnd.uniform(0, 360)).resize(size)
    bands = []
    for _ in range(3):
        lo = rnd.randint(0, 120)
        hi = rnd.randint(135, 255)
        bands.append(grad.point(lambda p, lo=lo, hi=hi: lo + p * (hi - lo) // 255))
    img = Image.merge("RGB", bands)
    d = ImageDraw.Draw(img)
    for _ in range(rnd.randint(8, 16)):
        x0 = rnd.randint(0, size[0] - 40)
        y0 = rnd.randint(0, size[1] - 40)
        x1 = x0 + rnd.randint(20, 200)
        y1 = y0 + rnd.randint(20, 160)
        color = tuple(rnd.randint(0, 255) for _ in range(3))
        if rnd.random() < 0.5:
            d.ellipse([x0, y0, x1, y1], fill=color)
        else:
            d.rectangle([x0, y0, x1, y1], fill=color)
    noise = Image.effect_noise(size, 12).convert("RGB")
    return Image.blend(img, noise, 0.08)


def make_sprite(seed: int, size: tuple[int, int] = BASE_SIZE) -> Image.Image:
    """白背景に、輪郭線つきの図形でできたキャラ風の絵を小さめに描く (seed ごとに別内容)。

    画像の大半が白背景になるため、別の絵同士でもピクセル照合の trimmed mean が小さくなる。
    位置・大きさ・髪型・服の形・ポーズを seed ごとに変える。横位置は seed ごとにずらす
    (同じ位置・同じ構図の別キャラは実データでも構造スコア 0.3 前後になり、標準では
    誤検出として残りうる。このテストは「描かれた位置や形が違う別の絵」を落とせるかを見る)。
    """
    rnd = random.Random(10_000 + seed)
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)

    def color() -> tuple[int, int, int]:
        return tuple(rnd.randint(30, 230) for _ in range(3))

    sc = rnd.uniform(0.6, 1.1)
    cx = int(w * (0.22 + 0.11 * (seed % 6))) + rnd.randint(-10, 10)
    top = rnd.randint(15, 60)
    head = int(rnd.randint(32, 52) * sc)
    body_w, body_h = int(rnd.randint(50, 100) * sc), int(rnd.randint(100, 160) * sc)
    outline = (40, 30, 30)
    hair = color()
    by = top + 2 * head
    # 髪 (後ろ髪) → 胴体 → 腕・脚 → 顔 → 前髪 → 模様 → 目
    style = rnd.randrange(3)
    if style == 1:   # ロング
        d.rectangle([cx - head - 8, top + head, cx + head + 8, by + int(body_h * rnd.uniform(0.4, 0.9))],
                    fill=hair, outline=outline, width=3)
    elif style == 2:  # ツインテール
        for side in (-1, 1):
            tx = cx + side * (head + 14)
            d.ellipse([tx - 14, top + head // 2, tx + 14, top + head // 2 + int(rnd.randint(70, 130) * sc)],
                      fill=hair, outline=outline, width=3)
    shape = rnd.randrange(3)
    if shape == 0:
        d.rectangle([cx - body_w // 2, by, cx + body_w // 2, by + body_h], fill=color(), outline=outline, width=3)
    elif shape == 1:
        d.ellipse([cx - body_w // 2, by, cx + body_w // 2, by + body_h], fill=color(), outline=outline, width=3)
    else:  # ワンピース (台形)
        d.polygon([(cx - body_w // 4, by), (cx + body_w // 4, by),
                   (cx + body_w // 2 + 20, by + body_h), (cx - body_w // 2 - 20, by + body_h)],
                  fill=color(), outline=outline, width=3)
    for side in (-1, 1):
        ang = math.radians(rnd.uniform(-150, 60))
        ln = rnd.randint(50, 100) * sc
        sx, sy = cx + side * body_w // 2, by + 15
        d.line([sx, sy, sx + side * ln * math.cos(ang), sy - ln * math.sin(ang)], fill=outline, width=7)
        lx = cx + side * body_w // 4
        d.line([lx, by + body_h, lx + side * rnd.randint(0, 30), min(h - 5, by + body_h + rnd.randint(25, 60))],
               fill=outline, width=8)
    d.ellipse([cx - head, top + 8, cx + head, top + 2 * head], fill=(250, 225, 210), outline=outline, width=3)
    d.chord([cx - head - 6, top, cx + head + 6, top + int(head * rnd.uniform(1.0, 1.6))],
            180, 360, fill=hair, outline=outline, width=3)
    for _ in range(rnd.randint(2, 6)):
        x0 = cx - body_w // 2 + rnd.randint(0, max(1, body_w - 15))
        y0 = by + rnd.randint(0, max(1, body_h - 15))
        d.rectangle([x0, y0, x0 + rnd.randint(8, 24), y0 + rnd.randint(6, 24)], fill=color())
    ey = top + head + rnd.randint(0, 12)
    for side in (-1, 1):
        ex = cx + side * head // 2
        d.ellipse([ex - 6, ey - 8, ex + 6, ey + 8], fill=color(), outline=outline, width=2)
    return img


def add_patch(img: Image.Image, area_ratio: float, rnd: random.Random) -> Image.Image:
    """絵の部分 (白以外) の一部を別の色の円で塗り替える (表情・小物の差分を模擬)。"""
    img = img.copy()
    w, h = img.size
    side = int((w * h * area_ratio) ** 0.5)
    gray = img.convert("L")
    for _ in range(200):  # 円の中心が絵の上に来る位置を探す
        x0, y0 = rnd.randint(0, w - side), rnd.randint(0, h - side)
        if gray.getpixel((x0 + side // 2, y0 + side // 2)) < 235:
            break
    ImageDraw.Draw(img).ellipse([x0, y0, x0 + side, y0 + side],
                                fill=tuple(rnd.randint(0, 255) for _ in range(3)))
    return img


def add_text_like(img: Image.Image, area_ratio: float, rnd: random.Random) -> Image.Image:
    """画像の area_ratio 分の帯に疑似文字 (白帯 + 黒矩形列) を描く。"""
    img = img.copy()
    d = ImageDraw.Draw(img)
    w, h = img.size
    band_h = max(int(h * area_ratio), 8)
    y0 = rnd.randint(0, h - band_h)
    d.rectangle([0, y0, w, y0 + band_h], fill=(255, 255, 255))
    x = 8
    while x < w - 30:
        cw = rnd.randint(8, 24)
        ch = rnd.randint(max(band_h * 2 // 5, 4), max(band_h * 4 // 5, 5))
        cy = y0 + (band_h - ch) // 2
        d.rectangle([x, cy, x + cw, cy + ch], fill=(0, 0, 0))
        x += cw + rnd.randint(4, 12)
    return img


def make_variants(base: Image.Image, seed: int, out_dir: Path, stem: str) -> dict[str, Path]:
    """元画像から変種を生成してファイル保存し {変種名: パス} を返す。"""
    rnd = random.Random(seed * 7919 + 1)
    w, h = base.size
    paths: dict[str, Path] = {}

    def save(name: str, img: Image.Image, *, quality: int = 90) -> None:
        p = out_dir / f"{stem}_{name}.jpg"
        img.convert("RGB").save(p, quality=quality)
        paths[name] = p

    orig = out_dir / f"{stem}_orig.png"
    base.save(orig)
    paths["orig"] = orig
    save("resize50", base.resize((w // 2, h // 2), Image.Resampling.LANCZOS))
    save("resize33", base.resize((w // 3, h // 3), Image.Resampling.LANCZOS))
    save("jpeg40", base, quality=40)
    save("jpeg80", base, quality=80)
    save("bright+20", ImageEnhance.Brightness(base).enhance(1.2))
    save("bright-20", ImageEnhance.Brightness(base).enhance(0.8))
    save("contrast08", ImageEnhance.Contrast(base).enhance(0.8))
    save("text10", add_text_like(base, 0.10, rnd))
    save("text25", add_text_like(base, 0.25, rnd))
    save("patch05", add_patch(base, 0.05, rnd))
    return paths


def load_bases(image_dir: Path | None) -> list[Image.Image]:
    sprites = [make_sprite(seed) for seed in range(SPRITE_COUNT)]
    if image_dir is None:
        return [make_base(seed) for seed in range(BASE_COUNT)] + sprites
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    files = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in exts)[:10]
    if not files:
        print(f"エラー: {image_dir} に画像がありません")
        sys.exit(1)
    print(f"実画像 {len(files)} 枚を元画像として使用: {image_dir}")
    return [Image.open(p).convert("RGB") for p in files] + sprites


# ---------------------------------------------------------------------------
# スコア行列・分類テスト
# ---------------------------------------------------------------------------

def run_verification(image_dir: Path | None) -> int:
    bases = load_bases(image_dir)
    trims = [p.trim_ratio for p in PRESETS]

    with tempfile.TemporaryDirectory(prefix="pixella_verify_") as tmp:
        out_dir = Path(tmp)
        # 生成 + ハッシュ計算
        data: list[dict] = []  # {base, name, phash, dhash, gray}
        for bi, base in enumerate(bases):
            for name, path in make_variants(base, bi, out_dir, f"b{bi}").items():
                ph, dh, gray, _w, _h = compute_image_hashes(str(path))
                data.append({"base": bi, "name": name, "phash": ph, "dhash": dh, "gray": gray})

        norm = {i: normalize_gray(d["gray"]) for i, d in enumerate(data)}
        planes = {i: structure_planes(d["gray"]) for i, d in enumerate(data)}

        def pair_row(i: int, j: int) -> tuple[int, int, list[float], float]:
            dd = hamming(data[i]["dhash"], data[j]["dhash"])
            pd = hamming(data[i]["phash"], data[j]["phash"])
            scores = [pixel_score(norm[i], norm[j], t) for t in trims]
            st = structure_score(data[i]["gray"], planes[i], data[j]["gray"], planes[j],
                                 norm_difference(norm[i], norm[j]))
            return dd, pd, scores, (1.0 if st is None else st)

        idx = {(d["base"], d["name"]): i for i, d in enumerate(data)}

        # --- ターゲットペア (orig vs 各変種) ---
        print()
        print(f"ハッシュゲート: dHash<={GATE_DHASH_MAX} AND pHash<={GATE_PHASH_MAX}")
        header = f'{"ペア":<27}{"dH":>4}{"pH":>4}{"gate":>6}' + "".join(
            f"  trim{int(t*100):02d}" for t in trims) + "  struct"
        print()
        print("=== ターゲットペア (元画像 vs 変種) — 検出したい ===")
        print(header)
        gate_miss = 0
        sprite_gate_miss: list[str] = []
        first_sprite = len(bases) - SPRITE_COUNT
        target_max = {t: 0.0 for t in trims}
        per_variant_max: dict[str, list[float]] = {}
        per_variant_struct_min: dict[str, float] = {}
        for bi in range(len(bases)):
            for name in VARIANTS_EXPECT:
                i, j = idx[(bi, "orig")], idx[(bi, name)]
                dd, pd, scores, st = pair_row(i, j)
                per_variant_struct_min[name] = min(per_variant_struct_min.get(name, 1.0), st)
                ok = dd <= GATE_DHASH_MAX and pd <= GATE_PHASH_MAX
                if not ok:
                    if bi >= first_sprite:
                        sprite_gate_miss.append(f"b{bi}:{name}")
                    else:
                        gate_miss += 1
                for t, s in zip(trims, scores):
                    target_max[t] = max(target_max[t], s)
                mx = per_variant_max.setdefault(name, [0.0] * len(trims))
                for k, s in enumerate(scores):
                    mx[k] = max(mx[k], s)
                print(f'b{bi:<2}:orig vs {name:<14}{dd:>4}{pd:>4}{"通過" if ok else "×漏れ":>5}'
                      + "".join(f"{s:>8.2f}" for s in scores) + f"{st:>8.2f}")

        # --- 無関係ペア (別の元画像同士の orig) ---
        print()
        print("=== 無関係ペア (別の元画像同士) — 検出したくない ===")
        print(header)
        unrelated_min = {t: 255.0 for t in trims}
        # ピクセル照合を通過してしまう無関係ペアの件数と構造スコア最大値 (プリセット別)
        unrelated_struct_max = [0.0] * len(PRESETS)
        unrelated_pixel_pass = [0] * len(PRESETS)
        for bi in range(len(bases)):
            for bj in range(bi + 1, len(bases)):
                i, j = idx[(bi, "orig")], idx[(bj, "orig")]
                dd, pd, scores, st = pair_row(i, j)
                gated = dd <= GATE_DHASH_MAX and pd <= GATE_PHASH_MAX
                for t, s in zip(trims, scores):
                    unrelated_min[t] = min(unrelated_min[t], s)
                for k, preset in enumerate(PRESETS):
                    if scores[k] <= preset.pixel_max:
                        unrelated_pixel_pass[k] += 1
                        unrelated_struct_max[k] = max(unrelated_struct_max[k], st)
                print(f'b{bi:<2}:orig vs b{bj:<2}:orig    {dd:>4}{pd:>4}'
                      f'{"通過" if gated else "遮断":>5}'
                      + "".join(f"{s:>8.2f}" for s in scores) + f"{st:>8.2f}")

        # --- サマリ ---
        print()
        print("=== サマリ ===")
        print(f"ハッシュゲート漏れ: {gate_miss} ペア (0 であるべき)")
        # 白背景の画像に文字帯を重ねると pHash/dHash が大きく変わり、ゲートで漏れることがある (既知の限界)。
        # 同じ元画像の他の変種を介して同じセットに入ることはあるため、end-to-end で確認する。
        print(f"白背景キャラのハッシュゲート漏れ (参考・既知の限界): {sprite_gate_miss or 'なし'}")
        for k, preset in enumerate(PRESETS):
            t = preset.trim_ratio
            names = [n for n, lv in VARIANTS_EXPECT.items() if lv <= k]
            tmax = max(per_variant_max[n][k] for n in names)
            smin = min(per_variant_struct_min[n] for n in names)
            print(f"[{preset.label}] trim={t:.2f} pixel_max={preset.pixel_max}: "
                  f"対象変種の最大スコア={tmax:.2f} / 無関係ペアの最小スコア={unrelated_min[t]:.2f}")
            print(f"        struct_min={preset.struct_min}: 対象変種の最小構造スコア={smin:.2f} / "
                  f"ピクセル照合を通過した無関係ペア {unrelated_pixel_pass[k]} 件の最大構造スコア="
                  f"{unrelated_struct_max[k]:.2f}")

        # --- end-to-end 分類テスト ---
        print()
        print("=== end-to-end (find_similar_clusters) ===")
        entries = [(i, d["phash"], d["dhash"], d["gray"]) for i, d in enumerate(data)]
        failures = 0
        for k, preset in enumerate(PRESETS):
            clusters = find_similar_clusters(entries, preset)
            cluster_of = {}
            for ci, ids in enumerate(clusters):
                for i in ids:
                    cluster_of[i] = ci
            # 1) 別 base のアイテムが同一クラスタに入っていないか (誤検出)
            cross = sum(
                1 for ids in clusters
                if len({data[i]["base"] for i in ids}) > 1
            )
            # 2) 期待される変種が orig と同一クラスタか (検出漏れ)
            miss = []
            for bi in range(len(bases)):
                oi = idx[(bi, "orig")]
                for name, lv in VARIANTS_EXPECT.items():
                    if lv > k:
                        continue
                    vi = idx[(bi, name)]
                    if cluster_of.get(oi) is None or cluster_of.get(vi) != cluster_of.get(oi):
                        miss.append(f"b{bi}:{name}")
            status = "PASS" if (cross == 0 and not miss) else "FAIL"
            if status == "FAIL":
                failures += 1
            print(f"[{preset.label}] {status}: クラスタ数={len(clusters)} "
                  f"混在クラスタ={cross} 検出漏れ={miss if miss else 'なし'}")

        return 1 if (failures or gate_miss) else 0


# ---------------------------------------------------------------------------
# 性能ベンチ
# ---------------------------------------------------------------------------

def run_bench(n: int = 10_000) -> None:
    rnd = random.Random(42)
    entries = [
        (i, rnd.getrandbits(64), rnd.getrandbits(64), rnd.randbytes(1024))
        for i in range(n)
    ]
    t0 = time.perf_counter()
    clusters = find_similar_clusters(entries, PRESETS[1])
    dt = time.perf_counter() - t0
    print(f"find_similar_clusters: {n} 件 (ランダム) → {dt:.1f} 秒, クラスタ数={len(clusters)}")

    # 偏りケース: 3 割の画像が平坦な行 (dHash 上位 4 行 = 0) を共有する
    skew = []
    for i in range(n):
        dh = rnd.getrandbits(64)
        if i % 10 < 3:
            dh &= (1 << 32) - 1  # 上位 4 行をゼロに (空・白背景を模擬)
        skew.append((i, rnd.getrandbits(64), dh, rnd.randbytes(1024)))
    t0 = time.perf_counter()
    clusters = find_similar_clusters(skew, PRESETS[1])
    dt = time.perf_counter() - t0
    print(f"find_similar_clusters: {n} 件 (平坦行 30%) → {dt:.1f} 秒, クラスタ数={len(clusters)}")

    ra, rb = rnd.randbytes(1024), rnd.randbytes(1024)
    ga, gb = normalize_gray(ra), normalize_gray(rb)
    reps = 2000
    t0 = time.perf_counter()
    for _ in range(reps):
        pixel_score(ga, gb, 0.25)
    dt = (time.perf_counter() - t0) / reps
    print(f"pixel_score: {dt * 1e6:.0f} µs/ペア")

    t0 = time.perf_counter()
    for _ in range(200):
        pa = structure_planes(ra)
    dt = (time.perf_counter() - t0) / 200
    print(f"structure_planes: {dt * 1e3:.2f} ms/画像")
    pb = structure_planes(rb)
    diff = norm_difference(ga, gb)
    t0 = time.perf_counter()
    for _ in range(reps):
        structure_score(ra, pa, rb, pb, diff)
    dt = (time.perf_counter() - t0) / reps
    print(f"structure_score: {dt * 1e6:.0f} µs/ペア")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--bench" in args:
        run_bench()
        sys.exit(0)
    image_dir = Path(args[0]) if args else None
    sys.exit(run_verification(image_dir))
