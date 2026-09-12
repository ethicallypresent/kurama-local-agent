# PyInstaller spec for the Kurama console GUI.
# Built by scripts/publish_portable.py — run from the agent root.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

for pkg in ("textual", "rich", "mdit_py_plugins"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        hiddenimports.append(pkg)

hiddenimports += collect_submodules("core")
hiddenimports += collect_submodules("tui")
hiddenimports += collect_submodules("tools")
hiddenimports += [
    "core.loop",
    "core.llama_server",
    "core.brain",
    "core.paths",
    "core.event_bus",
    "core.steer",
    "core.complete",
    "core.context",
    "core.model",
    "core.config",
    "core.boot",
    "core.contracts",
    "core.maintain",
    "core.boundary",
    "core.launcher",
    "core.permission",
    "core.desktop",
    "core.reflection",
    "core.resources",
    "tools._core.file_tools",
    "tools._core.web_tools",
    "tools._core.code_runner",
    "tui.app",
    "tui.stream",
    "tui.history",
    "tui.runtime",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["KuramaStudio"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Kurama",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Kurama",
)
