# pixella.spec — PyInstaller build spec
# Usage: pyinstaller pixella.spec

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[('resources', 'resources')],
    hiddenimports=[
        'sqlalchemy.dialects.sqlite',
        'PIL._tkinter_finder',
        *collect_submodules('pixella'),
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

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
    upx=True,
    console=False,       # GUI app — no console window
    icon='resources/icon.ico',
)
