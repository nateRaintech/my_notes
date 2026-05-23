# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller build spec for the my_notes desktop app (M5 packaging).
#
#   Build:   pyinstaller my_notes.spec        (run from the repo root)
#   Output:  dist/my_notes.exe                (single-file, windowed)
#
# A one-file, windowed (console=False) build of the app.py entry point. The
# only runtime data file is the dark-theme stylesheet, bundled under
# resources/ so core.theme.load_stylesheet("dark") finds it via sys._MEIPASS in
# the frozen exe (see core/theme.py:_resources_dir).
#
# No --hidden-import / --collect-binaries flags are needed for PySide6 or the
# SQLCipher driver: PyInstaller's bundled hooks pick up the sqlcipher3 C
# extension + DLL and the Qt plugins automatically (confirmed by the M2 spike,
# spikes/sqlcipher_windows/README.md). build/ and dist/ are git-ignored.


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('resources/dark.qss', 'resources')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='my_notes',
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
