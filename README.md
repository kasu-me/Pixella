# Pixella

**画像整理アプリ** — タグ付け・グループ化・高速サムネイルグリッド

## 機能

- PNG / JPEG / GIF / WebP に対応
- タグ付け（オートコンプリート付き）
- タグ AND 検索
- 画像のグループ化・解除
- グループのドリルイン表示（グループをクリックで中身を表示）
- 画像からグループを逆引き（詳細パネルに表示）
- グループへのタグ付け
- サムネイルキャッシュ（10000枚超対応）
- ライト / ダークモード切り替え
- データを JSON で書き出し
- ドラッグ&ドロップで画像を追加

## セットアップ

```powershell
cd pixella
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

## exe 化 (スタンドアロン配布)

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
└── pixella/
    ├── __init__.py
    ├── core/
    │   ├── config.py        # データパス設定
    │   ├── thumbnails.py    # サムネイルキャッシュ
    │   └── workers.py       # バックグラウンドスレッド
    ├── db/
    │   ├── models.py        # SQLAlchemy モデル
    │   └── repository.py   # DB 操作
    └── ui/
        ├── main_window.py   # メインウィンドウ
        ├── grid_view.py     # サムネイルグリッド
        ├── detail_panel.py  # 詳細パネル
        ├── tag_input.py     # タグ入力ウィジェット
        ├── search_bar.py    # 検索バー
        ├── dialogs.py       # グループ作成ダイアログ
        └── themes.py        # ライト/ダークテーマ
```
