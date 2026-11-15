# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/AUGUSTUS/Downloads/gemini/config.json', '.'), ('C:/Users/AUGUSTUS/Downloads/gemini/unit_registry.json', '.'), ('C:/Users/AUGUSTUS/Downloads/gemini/hsr_currency_wars_v4_7_strategies-v4.json', '.'), ('C:/Users/AUGUSTUS/Downloads/gemini/templates', 'templates')]
binaries = []
hiddenimports = ['cv2', 'numpy', 'keyboard', 'win32api', 'win32con', 'win32gui', 'wgc_python', 'cw_core', 'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'pytesseract', 'ctypes', 'difflib']
tmp_ret = collect_all('cv2')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('wgc_python')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['C:/Users/AUGUSTUS/Downloads/gemini/currency_wars_app-v9.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='CurrencyWarsAutomation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
