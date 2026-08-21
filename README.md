# Pixella

**画像整理アプリ** — タグ付け・グループ化・高速サムネイルグリッド

## 機能

- PNG / JPEG / GIF / WebP に対応
- タグ付け（オートコンプリート付き）
- タグ AND / OR 検索
- タグなし絞り込み（タグが未設定の画像・グループのみ表示）
- レーティング（画像・グループとも星0〜5、デフォルト0。詳細パネルの星クリックまたは右クリックメニューで設定。グループはタグ同様に独立した星を持つ）
  - グループ化／結合するとメンバー画像の星は0にリセットされる
  - グループを解除するとグループの星がメンバー画像すべてに適用される
- レーティングによる絞り込み（星の数を指定し ≧ / = / ≦ で比較。画像・グループ両方が対象）
- 複数選択での一括タグ付け・一括タグ削除
- 画像のグループ化・解除
- グループをクリックで別ウィンドウ表示
- グループ名のリネーム（F2 キーでも可能）
- グループからの画像除外
- 画像からグループを逆引き（詳細パネルに表示）
- グループへのタグ付け
- タグ管理ダイアログ（タグ一覧・色設定・削除・使用枚数表示・ソート）
- タグに色を設定（サムネイル・詳細パネルで色識別）
- 並び替え（追加順 / 作成日順 / 名前順、昇順・降順切り替え）
- ブレッドクラムナビゲーション
- サムネイルキャッシュ（10000枚超対応）
- ライト / ダークモード切り替え
- データを JSON で書き出し / 読み込み
- ドラッグ&ドロップで画像を追加
- ウィンドウサイズ・ソート設定の自動保存・復元
- マルチアルバム対応（作成・名前変更・削除・切り替え。ソート設定はアルバムごとに保存）
- 類似画像検出（Ctrl+D。dHash/pHash によるクラスタリング、厳密 / 標準 / ゆるめの3プリセット、重複画像の完全削除・管理からの除外・グループ化、誤検出セットの「今後表示しない」指定と解除）
- 正規表現でファイル名を指定してグループ化
- タグのコピー＆貼り付け（単一・複数選択の共通タグに対応）
- 全アルバムをまとめて1つの JSON に書き出し
- Windows IME の先頭文字入力落ち対策

## 動作要件

- Python 3.11 以上
- 依存パッケージは `requirements.txt` を参照

## セットアップ

```powershell
cd pixella
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## exe 化 (スタンドアロン)

```powershell
pip install pyinstaller
pyinstaller pixella.spec
# dist\Pixella.exe が生成される
```

## データ保存場所

`%APPDATA%\Pixella\`

- `albums\{uuid}.db` — アルバムごとの SQLite データベース（タグ・グループ情報）。マルチアルバムに対応しており、旧バージョンの `pixella.db` は初回起動時にデフォルトアルバムへ自動移行される
- `albums_meta.json` — アルバム一覧とアクティブアルバム
- `thumbnails\` — サムネイルキャッシュ
- `exports\` — JSON 書き出し先
- `icons\` — テーマ用インジケータアイコン（自動生成）

## ディレクトリ構成

```
pixella/
├── main.py                  # エントリポイント
├── requirements.txt
├── pyproject.toml
├── pixella.spec             # PyInstaller ビルド設定
├── resources/               # アイコン等のリソース
└── pixella/
    ├── __init__.py          # バージョン情報
    ├── core/
    │   ├── config.py         # データパス設定
    │   ├── album_manager.py  # マルチアルバム管理
    │   ├── thumbnails.py     # サムネイルキャッシュ
    │   ├── similarity.py     # 類似画像検出のハッシュ計算・クラスタリング
    │   └── workers.py        # バックグラウンドスレッド
    ├── db/
    │   ├── models.py         # SQLAlchemy モデル (Image / Group / Tag)
    │   └── repository.py     # DB 操作
    └── ui/
        ├── main_window.py     # メインウィンドウ
        ├── grid_view.py       # サムネイルグリッド
        ├── detail_panel.py    # 詳細パネル（プレビュー・タグ・グループ情報）
        ├── tag_input.py       # タグ入力ウィジェット
        ├── tag_manager.py     # タグ管理ダイアログ
        ├── search_bar.py      # 検索バー（AND/OR・タグなし絞り込み）
        ├── sort_bar.py        # 並び替えコントロール
        ├── breadcrumb.py      # ブレッドクラムナビゲーション
        ├── group_window.py    # グループ別ウィンドウ
        ├── dialogs.py         # グループ作成ダイアログ
        ├── similarity_dialog.py  # 類似画像検出ダイアログ
        ├── star_rating.py     # 星評価ウィジェット
        ├── rating_bar.py      # レーティング絞り込みバー
        ├── fluent_icons.py    # アイコン生成
        ├── ime_fix.py         # Windows IME 先頭文字落ち対策
        └── themes.py          # ライト/ダークテーマ
```

## アンインストール

アプリを完全に削除するには、以下の手順を実施してください。

1. **仮想環境・ソースを削除**
   ```powershell
   Remove-Item -Recurse -Force d:\path\to\pixella
   ```

2. **データディレクトリを削除**（タグ・グループ情報・サムネイルキャッシュがすべて消えます）
   ```powershell
   Remove-Item -Recurse -Force "$env:APPDATA\Pixella"
   ```

3. **ウィンドウ設定（レジストリ）を削除**
   ```powershell
   Remove-Item -Recurse -Force "HKCU:\Software\Pixella"
   ```

> [!WARNING]
> 手順 2 を実行すると全アルバムのデータベース (`albums\*.db`) およびサムネイルキャッシュが完全に削除されます。事前に JSON 書き出し（Ctrl+E）でバックアップを取ることを推奨します。

## 関連プロジェクト
本アプリからエクスポートしたJSONファイルをもとに、画像の検索･閲覧ができるWebアプリ [Pixella Web Viewer](https://github.com/kasu-me/Pixella-Web-Viewer) もご利用ください。
