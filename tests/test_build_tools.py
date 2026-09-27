"""Guards for the medium-severity audit findings in the packaging path.

These cover tooling rather than the app, but the failure modes were real: a
verifier that could not print its own verdict, and one that rewrote the
developer's Windows configuration as a side effect of checking a build.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify_build():
    return _load("verify_build")


# --- finding 9: the verifier destroyed its own stdout ---------------------


def test_console_probe_runs_out_of_process(verify_build, monkeypatch):
    """FreeConsole detaches the *calling* process from its console.

    Calling it inline closed this tool's stdout handle, so every later line -
    including the PASS/FAIL verdict it exists to print - went nowhere. The
    probe has to happen in a child.
    """
    calls: list[list[str]] = []

    class Result:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return Result()

    monkeypatch.setattr(verify_build.subprocess, "run", fake_run)
    verify_build.check_no_console(1234)

    assert calls, "the probe must run as a subprocess"
    assert calls[0][0] == sys.executable
    assert "1234" in calls[0]


def test_the_tool_never_frees_its_own_console(verify_build):
    source = (ROOT / "tools" / "verify_build.py").read_text(encoding="utf-8")
    body = source.split("_PROBE")[0]
    assert "FreeConsole" not in body, (
        "FreeConsole outside the child probe closes this process's stdout"
    )


def test_verification_does_not_touch_windows_configuration(verify_build):
    """Checking a build must not author real Startup/Start menu shortcuts."""
    source = (ROOT / "tools" / "verify_build.py").read_text(encoding="utf-8")
    assert "--no-windows-integration" in source


def test_pe_check_is_not_reimplemented(verify_build):
    """The app already has this; a second copy is a second thing to get wrong."""
    source = (ROOT / "tools" / "verify_build.py").read_text(encoding="utf-8")
    assert "is_gui_executable" in source
    assert "0x3C" not in source, "PE offsets should not be parsed here as well"


def test_subsystem_check_accepts_the_apps_own_verdict(verify_build, tmp_path):
    exe = tmp_path / "fake.exe"
    # Synthetic console-subsystem PE.
    pe_off = 0x80
    data = bytearray(pe_off + 24 + 70)
    data[0:2] = b"MZ"
    data[0x3C:0x40] = pe_off.to_bytes(4, "little")
    data[pe_off : pe_off + 4] = b"PE\0\0"
    data[pe_off + 24 + 68 : pe_off + 24 + 70] = (3).to_bytes(2, "little")
    exe.write_bytes(bytes(data))

    with pytest.raises(verify_build.Failure, match="console"):
        verify_build.check_subsystem(exe)
