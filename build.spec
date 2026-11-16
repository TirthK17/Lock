# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build specification for Personal Vault.
Build with:
    pyinstaller build.spec
"""

import sys
from pathlib import Path

block_cipher = None

root_dir = Path.cwd().resolve()

added_files = [
    (str(root_dir / "mylocker" / "docs"), "docs"),
    (str(root_dir / "mylocker" / "app" / "ui" / "resources"), "mylocker/app/ui/resources"),
]

hidden_imports = [
    "argon2",
    "argon2.low_level",
    "cryptography",
    "cryptography.hazmat.primitives.ciphers.aead",
    "cryptography.hazmat.primitives.kdf.hkdf",
    "win32security",
    "win32api",
    "win32con",
    "win32file",
    "win32pipe",
    "win32event",
    "win32process",
    "win32timezone",
    "win32crypt",
    "ntsecuritycon",
    "psutil",
    "PIL",
    "zxcvbn",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "cv2",
    "numpy",
]

excludes = [
    "tkinter",
    "unittest",
    "pdb",
    "pystray",
    "scipy",
    "matplotlib",
    "pandas",
    "torch",
    "torchvision",
    "ultralytics",
    "pytest",
    "hypothesis",
    "IPython",
    "PIL.AvifImagePlugin",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.QtQmlModels",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DAnimation",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects",
    "PySide6.QtHttpServer",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
]

a = Analysis(
    [str(root_dir / "mylocker" / "__main__.py")],
    pathex=[str(root_dir)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

_DROP_BINARY_FRAGMENTS = (
    "opengl32sw.dll",
    "qt6pdf",
    "qpdf.dll",
    "qt6quick",
    "qt6qml",
    "qt6webengine",
    "qt63d",
    "qt6charts",
    "qt6graphs",
    "qt6designer",
    "qt6sql",
    "\\xampp\\",
    "_avif.",
)
a.binaries = [
    b for b in a.binaries
    if not any(frag in (b[0] + "|" + b[1]).lower() for frag in _DROP_BINARY_FRAGMENTS)
]
a.datas = [
    d for d in a.datas
    if not any(frag in (d[0] + "|" + d[1]).lower() for frag in ("qt6\\translations", "pyside6\\translations"))
]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PersonalVault",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX explicitly disabled to prevent AV heuristics false-positives
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # Windows GUI mode (no black console window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
