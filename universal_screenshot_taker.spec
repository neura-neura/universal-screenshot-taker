# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


hiddenimports = [
    "pypdfium2",
    "docx",
    "ebooklib",
    "ebooklib.epub",
]
hiddenimports += collect_submodules("selenium.webdriver.edge")
hiddenimports += collect_submodules("selenium.webdriver.chrome")
hiddenimports += collect_submodules("selenium.webdriver.common")
hiddenimports += collect_submodules("selenium.webdriver.remote")
datas = []
binaries = []

datas += [("assets/app_icon.ico", "assets"), ("assets/app_icon.png", "assets")]

datas += collect_data_files("pypdfium2")
binaries += collect_dynamic_libs("pypdfium2")

datas += collect_data_files("docx")

datas += collect_data_files("ebooklib")

excludes = [
    "ocrmypdf",
    "pikepdf",
    "surya",
    "transformers",
    "torch",
    "numpy",
    "pandas",
    "matplotlib",
    "IPython",
    "notebook",
    "jupyterlab",
    "pytest",
    "tkinter",
    "cv2",
]


a = Analysis(
    ["script.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    exclude_binaries=False,
    name="UniversalScreenshotTaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon="assets/app_icon.ico",
)
