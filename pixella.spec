# pixella.spec — PyInstaller build spec
# Usage: pyinstaller pixella.spec

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

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
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

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
