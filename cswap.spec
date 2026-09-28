# PyInstaller spec for the claude-swap build bundled with Claude Profiles.
#
# Build after claude_profiles.spec, into the app's folder:
#   pyinstaller --noconfirm --distpath "dist/Claude Profiles" cswap.spec
# Result:
#   dist/Claude Profiles/cswap/cswap.exe
#
# claude-swap itself comes from tools/cswap/requirements.txt, pinned and
# hash-locked. This is a console program on purpose: the tray app runs it with
# no window and reads its JSON, and people can also run it in a terminal.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

from importlib.metadata import version as _dist_version

CSWAP_VERSION = _dist_version("claude-swap")


def _version_resource() -> VSVersionInfo:
    numbers = tuple(int(part) for part in CSWAP_VERSION.split(".")[:3]) + (0,)
    strings = {
        "CompanyName": "realiti4 (bundled by Claude Profiles)",
        "FileDescription": "claude-swap",
        "FileVersion": CSWAP_VERSION,
        "InternalName": "cswap",
        "LegalCopyright": "MIT License. See THIRD-PARTY-NOTICES.txt.",
        "OriginalFilename": "cswap.exe",
        "ProductName": "claude-swap",
        "ProductVersion": CSWAP_VERSION,
    }
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[
            StringFileInfo(
                [StringTable("040904B0", [StringStruct(k, v) for k, v in strings.items()])]
            ),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )


# claude-swap reads its own version from package metadata at import time, and
# keyring discovers its Windows backend through entry points; both need the
# dist-info copied in or the frozen program fails on start.
datas = copy_metadata("claude-swap") + copy_metadata("keyring", recursive=True)
# Textual ships stylesheets as package data for claude-swap's terminal UI.
datas += collect_data_files("textual")

a = Analysis(
    [str(Path(SPECPATH) / "tools" / "cswap" / "cswap_entry.py")],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=collect_submodules("claude_swap") + collect_submodules("keyring.backends"),
    hookspath=[],
    runtime_hooks=[],
    # The macOS menu bar extra is never used on Windows.
    excludes=["rumps", "tkinter", "PySide6", "pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cswap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    version=_version_resource(),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cswap",
)
