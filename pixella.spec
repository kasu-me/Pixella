# pixella.spec — PyInstaller build spec
# Usage: pyinstaller pixella.spec

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('resources/icon.png', 'resources')],
    hiddenimports=[
        'sqlalchemy.dialects.sqlite',
        *collect_submodules('pixella'),
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'setuptools',
        'pkg_resources',
        'PySide6.QtNetwork',
    ],
    noarchive=False,
)

# 起動高速化: onefile は起動のたびに同梱物を一時フォルダへ展開するため、使わない Qt の
# DLL・プラグイン・翻訳、PIL の AVIF 拡張などを除いて展開量を減らす。
# Qt の DLL とプラグインは許可リスト方式（ここに無いものは同梱しない）。
_QT_DLLS = {'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll'}
_QT_PLUGINS = {
    'platforms/qwindows.dll',
    'styles/qmodernwindowsstyle.dll',
    'imageformats/qjpeg.dll',
    'imageformats/qgif.dll',
    'imageformats/qwebp.dll',
    'imageformats/qico.dll',
}
_DROP = {
    'pyside6/opengl32sw.dll',          # ソフトウェア OpenGL（Widgets アプリでは不要）
    'pil/_avif.cp312-win_amd64.pyd',   # AVIF は非対応（SUPPORTED_EXTS 外）
    'pil/_imagingtk.cp312-win_amd64.pyd',
}


def _keep(name):
    n = name.replace('\\', '/').lower()
    if n in _DROP or n.startswith('pyside6/translations/'):
        return False
    if n.startswith('pyside6/plugins/'):
        return n[len('pyside6/plugins/'):] in _QT_PLUGINS
    if n.startswith('pyside6/qt6') and n.endswith('.dll'):
        return n[len('pyside6/'):] in _QT_DLLS
    return True


a.binaries = [e for e in a.binaries if _keep(e[0])]
a.datas = [e for e in a.datas if _keep(e[0])]

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='Pixella',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,           # UPX 圧縮は起動時の展開を遅くするため使わない
    console=False,       # GUI app — no console window
    icon='resources/icon.ico',
)
