"""End-to-end check of a built ClaudeProfiles.exe.

Written after a build that passed a naive check and was broken anyway: the exe
had the right PE subsystem and "ran", but it was really PyInstaller's traceback
dialog sitting on an ImportError. Checking that a window exists, or that no
console appeared, proves nothing about whether the application started.

So this asserts the app actually reached its own code and did work:

* the PE header says windowed,
* no console is attached to the running process,
* it writes to a fresh data directory (only reachable after the service layer
  is up),
* it exits cleanly when asked,
* and stderr stayed empty.

Usage:  python tools/verify_build.py [path-to-exe]
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXE = ROOT / "dist" / "Claude Profiles" / "ClaudeProfiles.exe"

SUBSYSTEM_GUI = 2
STARTUP_GRACE_SECONDS = 25


class Failure(Exception):
    pass


def check_subsystem(exe: Path) -> None:
    data = exe.read_bytes()
    if data[:2] != b"MZ":
        raise Failure("not a PE binary")
    pe = int.from_bytes(data[0x3C:0x40], "little")
    if data[pe : pe + 4] != b"PE\0\0":
        raise Failure("bad PE header")
    subsystem = int.from_bytes(data[pe + 24 + 68 : pe + 24 + 70], "little")
    if subsystem != SUBSYSTEM_GUI:
        raise Failure(f"subsystem is {subsystem}, expected {SUBSYSTEM_GUI} (windowed)")
    print(f"  ok  windowed binary (PE subsystem {subsystem})")


def check_no_console(pid: int) -> None:
    kernel32 = ctypes.windll.kernel32
    kernel32.FreeConsole()
    if kernel32.AttachConsole(pid):
        kernel32.FreeConsole()
        raise Failure("a console is attached - a terminal would be visible")
    print("  ok  no console attached to the process")


def check_starts_and_works(exe: Path, data_dir: Path) -> None:
    for stale in ("activity.jsonl", "settings.json", "err.txt"):
        (data_dir / stale).unlink(missing_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    err_path = data_dir / "err.txt"
    with err_path.open("wb") as err:
        process = subprocess.Popen(
            [str(exe), "--mock", "--data-dir", str(data_dir)],
            stdout=subprocess.DEVNULL,
            stderr=err,
        )

    try:
        activity = data_dir / "activity.jsonl"
        deadline = time.monotonic() + STARTUP_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise Failure(
                    f"exited early with code {process.returncode}\n"
                    f"{err_path.read_text(errors='replace')[:2000]}"
                )
            if activity.is_file() and activity.stat().st_size > 0:
                break
            time.sleep(0.5)
        else:
            raise Failure(
                "started but never wrote an activity entry - the service layer "
                "did not come up\n"
                f"{err_path.read_text(errors='replace')[:2000]}"
            )
        print(f"  ok  reached its own code (wrote {activity.name})")

        check_no_console(process.pid)

        stderr_text = err_path.read_text(errors="replace").strip()
        if stderr_text:
            raise Failure(f"wrote to stderr:\n{stderr_text[:2000]}")
        print("  ok  stderr clean")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def main(argv: list[str]) -> int:
    exe = Path(argv[1]) if len(argv) > 1 else DEFAULT_EXE
    print(f"verifying {exe}")
    if not exe.is_file():
        print(f"  FAIL  not found: {exe}")
        return 1
    try:
        check_subsystem(exe)
        check_starts_and_works(exe, Path(exe.parent) / "_verify_data")
    except Failure as exc:
        print(f"  FAIL  {exc}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
