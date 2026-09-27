"""Guards on the entry point and the launcher-selection logic.

Both cover failures that a passing build hides. The frozen app once crashed
instantly on ``ImportError: attempted relative import with no known parent
package`` because PyInstaller runs the entry script *as* ``__main__``, with no
package context - and the exe still looked fine from the outside, because
PyInstaller's traceback dialog keeps the process alive.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from claude_profiles.services import autostart

ENTRY = Path(autostart.__file__).resolve().parents[1] / "__main__.py"


def test_entry_point_uses_absolute_imports_only():
    """A relative import here works under -m and breaks the frozen build."""
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    relative = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level and node.level > 0
    ]
    assert not relative, (
        "__main__.py must not use relative imports: PyInstaller executes it as "
        "__main__ with no parent package"
    )


def test_entry_point_imports_main_from_the_package():
    tree = ast.parse(ENTRY.read_text(encoding="utf-8"))
    targets = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "claude_profiles.app" in targets


def test_main_is_importable_by_absolute_path():
    from claude_profiles.app import main

    assert callable(main)


# --- windowed-binary detection --------------------------------------------


def test_gui_detection_on_a_missing_file(tmp_path):
    assert autostart.is_gui_executable(tmp_path / "nope.exe") is None


def test_gui_detection_on_a_non_pe_file(tmp_path):
    f = tmp_path / "not.exe"
    f.write_bytes(b"this is not a PE binary at all")
    assert autostart.is_gui_executable(f) is None


def test_gui_detection_identifies_a_console_binary():
    """A name is not a promise; the PE header is."""
    import sys

    console = Path(sys.executable)  # python.exe is a console binary
    if console.is_file():
        assert autostart.is_gui_executable(console) is False


@pytest.mark.parametrize("subsystem,expected", [(2, True), (3, False)])
def test_gui_detection_reads_the_subsystem_field(tmp_path, subsystem, expected):
    # Minimal synthetic PE: MZ header, e_lfanew, PE signature, then the
    # optional header with the subsystem at offset 68.
    pe_off = 0x80
    data = bytearray(pe_off + 24 + 70)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = pe_off.to_bytes(4, "little")
    data[pe_off : pe_off + 4] = b"PE\0\0"
    data[pe_off + 24 + 68 : pe_off + 24 + 70] = subsystem.to_bytes(2, "little")
    f = tmp_path / "synthetic.exe"
    f.write_bytes(bytes(data))
    assert autostart.is_gui_executable(f) is expected
