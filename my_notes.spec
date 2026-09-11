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
# No --collect-binaries flags are needed for PySide6 or the SQLCipher driver:
# PyInstaller's bundled hooks pick up the sqlcipher3 C extension + DLL and the
# Qt plugins automatically (confirmed by the M2 spike,
# spikes/sqlcipher_windows/README.md). The only hidden imports are the tools
# suite's two lazily-imported packages — see the note on hiddenimports below.
# build/ and dist/ are git-ignored.


# --- DLL resolution must prefer THIS environment (see LESSONS.md) ------------
#
# PyInstaller finds the DLLs a C extension depends on by searching the system
# PATH. On this machine another conda environment (Office_MCP) sits earlier on
# PATH, so the build silently paired *this* env's `pyexpat.pyd` with *that*
# env's `libexpat.dll` — along with sqlite3, bz2 and lzma. The mismatch isn't
# caught at build time: the exe builds cleanly and then dies on launch with
#
#     ImportError: DLL load failed while importing pyexpat:
#     The operating system cannot run %1.
#
# Putting the building interpreter's own directories at the front of PATH makes
# every dependency resolve within one environment. `sys.prefix` is the env
# running PyInstaller, so this stays correct wherever the project is built and
# is a no-op on a non-conda layout (the extra directories simply don't exist).
import os
import sys

_env_dirs = [
    sys.prefix,                                    # python.exe lives here
    os.path.join(sys.prefix, 'DLLs'),              # stdlib C extensions
    os.path.join(sys.prefix, 'Library', 'bin'),    # conda's native libraries
]
os.environ['PATH'] = os.pathsep.join(
    [d for d in _env_dirs if os.path.isdir(d)] + [os.environ.get('PATH', '')]
)


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[('resources/dark.qss', 'resources')],
    # The tools suite imports these two lazily, inside the tool that needs
    # them (core/tools/_util.py:lazy_import), so one missing package can't take
    # the whole suite down. PyInstaller's static analysis cannot see a runtime
    # __import__ of a variable module name, so they must be named here or the
    # frozen exe raises "requires pyyaml" for every YAML and SQL tool.
    hiddenimports=['yaml', 'sqlparse'],
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
