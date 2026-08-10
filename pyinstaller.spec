# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the single-file, no-console info-kierowca-notifier app.

Build with: pyinstaller pyinstaller.spec
(equivalent to `pyinstaller --onefile --windowed --name info-kierowca-notifier src/info_kierowca_notifier/app.py`,
kept as a spec file so the release workflow and any manual build use identical
settings on every platform.)
"""
from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["src/info_kierowca_notifier/app.py"],
    pathex=["src"],
    binaries=[],
    # The snapshots are package data. PyInstaller does not infer non-Python
    # resources, so preserve their installed package-relative paths explicitly.
    datas=[
        ("src/info_kierowca_notifier/data/word_centers.json", "info_kierowca_notifier/data"),
        ("src/info_kierowca_notifier/data/categories.json", "info_kierowca_notifier/data"),
    ] + collect_data_files("certifi"),
    hiddenimports=[
        "truststore", "certifi", "keyring.backends.Windows",
        "keyring.backends.macOS", "keyring.backends.SecretService",
        "keyring.backends.libsecret", "keyring.backends.kwallet",
    ],
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
