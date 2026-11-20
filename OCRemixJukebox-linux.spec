# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = collect_all('yt_dlp')
hiddenimports += ['yt_dlp.extractor', 'yt_dlp.downloader', 'vlc']

a = Analysis(
    ['ocremix.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'unittest', 'test', 'pydoc'],
    noarchive=False,
    optimize=0,
)
# System VLC discovers its plugins relative to its own libraries.
a.binaries = [
    entry for entry in a.binaries
    if not entry[0].startswith(('libvlc.so', 'libvlccore.so'))
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='OCRemixJukebox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
