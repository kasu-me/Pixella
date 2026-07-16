"""類似画像検出の検証・キャリブレーションスクリプト (Qt 非依存)。

元画像から変種 (リサイズ・再圧縮・明度変更・文字入れ等) と無関係画像を生成し、
ハッシュゲートの recall とピクセルスコアの分離度を実測する。
PRESETS のしきい値調整の根拠データを出力し、end-to-end の分類テストを行う。

使い方 (pixella/ ディレクトリから):
    python tools\verify_similarity.py            # 手続き生成画像で検証
    python tools\verify_similarity.py <画像DIR>  # 実画像を元画像として検証
    python tools\verify_similarity.py --bench    # 10,000 件の性能ベンチのみ
"""
from __future__ import annotations

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
    normalize_gray, pixel_score,
)

BASE_COUNT = 6          # 手続き生成する元画像の数
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
    return paths


def load_bases(image_dir: Path | None) -> list[Image.Image]:
    if image_dir is None:
        return [make_base(seed) for seed in range(BASE_COUNT)]
    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
    files = sorted(p for p in image_dir.iterdir() if p.suffix.lower() in exts)[:10]
    if not files:
        print(f"エラー: {image_dir} に画像がありません")
        sys.exit(1)
    print(f"実画像 {len(files)} 枚を元画像として使用: {image_dir}")
    return [Image.open(p).convert("RGB") for p in files]


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

        def pair_row(i: int, j: int) -> tuple[int, int, list[float]]:
            dd = hamming(data[i]["dhash"], data[j]["dhash"])
            pd = hamming(data[i]["phash"], data[j]["phash"])
            scores = [pixel_score(norm[i], norm[j], t) for t in trims]
            return dd, pd, scores

        idx = {(d["base"], d["name"]): i for i, d in enumerate(data)}

        # --- ターゲットペア (orig vs 各変種) ---
        print()
        print(f"ハッシュゲート: dHash<={GATE_DHASH_MAX} AND pHash<={GATE_PHASH_MAX}")
        header = f'{"ペア":<26}{"dH":>4}{"pH":>4}{"gate":>6}' + "".join(
            f"  trim{int(t*100):02d}" for t in trims)
        print()
        print("=== ターゲットペア (元画像 vs 変種) — 検出したい ===")
        print(header)
        gate_miss = 0
        target_max = {t: 0.0 for t in trims}
        per_variant_max: dict[str, list[float]] = {}
        for bi in range(len(bases)):
            for name in VARIANTS_EXPECT:
                i, j = idx[(bi, "orig")], idx[(bi, name)]
                dd, pd, scores = pair_row(i, j)
                ok = dd <= GATE_DHASH_MAX and pd <= GATE_PHASH_MAX
                if not ok:
                    gate_miss += 1
                for t, s in zip(trims, scores):
                    target_max[t] = max(target_max[t], s)
                mx = per_variant_max.setdefault(name, [0.0] * len(trims))
                for k, s in enumerate(scores):
                    mx[k] = max(mx[k], s)
                print(f'b{bi}:orig vs {name:<14}{dd:>4}{pd:>4}{"通過" if ok else "×漏れ":>5}'
                      + "".join(f"{s:>8.2f}" for s in scores))

        # --- 無関係ペア (別の元画像同士の orig) ---
        print()
        print("=== 無関係ペア (別の元画像同士) — 検出したくない ===")
        print(header)
        unrelated_min = {t: 255.0 for t in trims}
        unrelated_min_gated = {t: 255.0 for t in trims}
        for bi in range(len(bases)):
            for bj in range(bi + 1, len(bases)):
                i, j = idx[(bi, "orig")], idx[(bj, "orig")]
                dd, pd, scores = pair_row(i, j)
                gated = dd <= GATE_DHASH_MAX and pd <= GATE_PHASH_MAX
                for t, s in zip(trims, scores):
                    unrelated_min[t] = min(unrelated_min[t], s)
                    if gated:
                        unrelated_min_gated[t] = min(unrelated_min_gated[t], s)
                print(f'b{bi}:orig vs b{bj}:orig    {dd:>4}{pd:>4}'
                      f'{"通過" if gated else "遮断":>5}'
                      + "".join(f"{s:>8.2f}" for s in scores))

        # --- サマリ ---
        print()
        print("=== サマリ ===")
        print(f"ハッシュゲート漏れ: {gate_miss} ペア (0 であるべき)")
        for k, preset in enumerate(PRESETS):
            t = preset.trim_ratio
            names = [n for n, lv in VARIANTS_EXPECT.items() if lv <= k]
            tmax = max(per_variant_max[n][k] for n in names)
            print(f"[{preset.label}] trim={t:.2f} pixel_max={preset.pixel_max}: "
                  f"対象変種の最大スコア={tmax:.2f} / 無関係ペアの最小スコア={unrelated_min[t]:.2f}")

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

    ga = normalize_gray(rnd.randbytes(1024))
    gb = normalize_gray(rnd.randbytes(1024))
    reps = 2000
    t0 = time.perf_counter()
    for _ in range(reps):
        pixel_score(ga, gb, 0.25)
    dt = (time.perf_counter() - t0) / reps
    print(f"pixel_score: {dt * 1e6:.0f} µs/ペア")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--bench" in args:
        run_bench()
        sys.exit(0)
    image_dir = Path(args[0]) if args else None
    sys.exit(run_verification(image_dir))
