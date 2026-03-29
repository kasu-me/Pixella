# Pixella

**画像整理アプリ** — タグ付け・グループ化・高速サムネイルグリッド

## 機能

- PNG / JPEG / GIF / WebP に対応
- タグ付け（オートコンプリート付き）
- タグ AND / OR 検索
- タグなし絞り込み（タグが未設定の画像・グループのみ表示）
- 複数選択での一括タグ付け・一括タグ削除
- 画像のグループ化・解除
- グループをクリックで別ウィンドウ表示
- グループ名のリネーム
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

## 動作要件

- Python 3.11 以上
- PySide6 >= 6.7.0
- Pillow >= 10.3.0
- SQLAlchemy >= 2.0.0

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

- `pixella.db` — SQLite データベース（タグ・グループ情報）
- `thumbnails\` — サムネイルキャッシュ
- `exports\` — JSON 書き出し先

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
    │   ├── config.py        # データパス設定
    │   ├── thumbnails.py    # サムネイルキャッシュ
    │   └── workers.py       # バックグラウンドスレッド
    ├── db/
    │   ├── models.py        # SQLAlchemy モデル (Image / Group / Tag)
    │   └── repository.py    # DB 操作
    └── ui/
        ├── main_window.py   # メインウィンドウ
        ├── grid_view.py     # サムネイルグリッド
        ├── detail_panel.py  # 詳細パネル（プレビュー・タグ・グループ情報）
        ├── tag_input.py     # タグ入力ウィジェット
        ├── tag_manager.py   # タグ管理ダイアログ
        ├── search_bar.py    # 検索バー（AND/OR・タグなし絞り込み）
        ├── sort_bar.py      # 並び替えコントロール
        ├── breadcrumb.py    # ブレッドクラムナビゲーション
        ├── group_window.py  # グループ別ウィンドウ
        ├── dialogs.py       # グループ作成ダイアログ
        └── themes.py        # ライト/ダークテーマ
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
> 手順 2 を実行するとデータベース (`pixella.db`) およびサムネイルキャッシュが完全に削除されます。事前に JSON 書き出し（Ctrl+E）でバックアップを取ることを推奨します。
