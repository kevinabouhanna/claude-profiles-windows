# PyInstaller spec for Claude Profiles.
#
# Build with:  pyinstaller claude_profiles.spec
# Result:      dist/ClaudeProfiles.exe
#
# This bundles the UI only. claude-swap stays a separate installation, because
# credential handling deliberately lives outside this application.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

# Qt modules this app does not use; excluding them roughly halves the output.
EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "tkinter",
    "unittest",
    "pydoc",
]

a = Analysis(
    ["src/claude_profiles/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("claude_profiles"),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# The icon is generated rather than committed as a binary asset.
_icon_path = Path("build") / "claude_profiles.ico"
try:
    from claude_profiles.resources.icons import save_app_icon  # noqa: E402

    save_app_icon(_icon_path)
    _icon = str(_icon_path)
except Exception:  # pragma: no cover - packaging convenience only
    _icon = None

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ClaudeProfiles",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # tray app: no console window
    disable_windowed_traceback=False,
    icon=_icon,
)
