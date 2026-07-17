# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the single-file, no-console info-kierowca-notifier app.

Build with: pyinstaller pyinstaller.spec
(equivalent to `pyinstaller --onefile --windowed --name info-kierowca-notifier app.py`,
kept as a spec file so the release workflow and any manual build use identical
settings on every platform.)
"""

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="info-kierowca-notifier",
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
