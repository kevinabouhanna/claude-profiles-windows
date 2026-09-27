# PyInstaller spec for Claude Profiles.
#
# Build with:  pyinstaller claude_profiles.spec
# Result:      dist/ClaudeProfiles.exe
#
# console=False is the point of this build. A venv's pythonw.exe created by uv
# is a trampoline compiled for the *console* subsystem, so launching the app
# through it pops a terminal no matter what the file is called. A frozen GUI
# binary has no console to allocate.
#
# This bundles the UI only. claude-swap stays a separate installation, because
# credential handling deliberately lives outside this application.

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH) / "src"))

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

# Qt modules this app does not use; excluding them roughly halves the output.
EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtPositioning",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "tkinter",
    "unittest",
    "pydoc",
    "pytest",
]


def _app_icon() -> str | None:
    """Path to the pre-generated .ico, or None if it is not there.

    The icon is rendered by tools/make_icon.py *before* the build rather than
    here. Drawing it needs a QPixmap, and constructing one without a
    QGuiApplication makes Qt abort the process - which inside a spec file looks
    like PyInstaller crashing at the PYZ stage with no error message.
    """
    target = Path(SPECPATH) / "build" / "claude_profiles.ico"
    return str(target) if target.is_file() else None


a = Analysis(
    ["src/claude_profiles/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("claude_profiles"),
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# A one-folder build rather than one-file. One-file re-extracts ~45 MB to a
# temp directory on every launch, which is slow for something that starts with
# Windows, and it leaves a bootloader parent process alongside the real one.
# A folder under %LOCALAPPDATA%\Programs is also the shape an ordinary
# user-scoped Windows application already has.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClaudeProfiles",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # tray app: GUI subsystem, so no console is ever allocated
    disable_windowed_traceback=False,
    icon=_app_icon(),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Claude Profiles",
)
