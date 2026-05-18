# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['TradingBotControl.pyw'],
    pathex=['src'],
    binaries=[('C:\\Users\\26560\\AppData\\Local\\Programs\\Python\\Python314\\DLLs\\tcl86t.dll', '.'), ('C:\\Users\\26560\\AppData\\Local\\Programs\\Python\\Python314\\DLLs\\tk86t.dll', '.'), ('C:\\Users\\26560\\AppData\\Local\\Programs\\Python\\Python314\\DLLs\\_tkinter.pyd', '.')],
    datas=[('C:\\Users\\26560\\AppData\\Local\\Programs\\Python\\Python314\\tcl\\tcl8.6', 'tcl\\tcl8.6'), ('C:\\Users\\26560\\AppData\\Local\\Programs\\Python\\Python314\\tcl\\tk8.6', 'tcl\\tk8.6'), ('src\\trading_bot\\config\\risk_limits.yaml', 'trading_bot\\config')],
    hiddenimports=['tkinter', 'tkinter.ttk'],
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
    name='TradingBotControl',
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
