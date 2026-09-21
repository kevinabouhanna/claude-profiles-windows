"""Optional "launch at Windows sign-in" support.

Implemented as a shortcut in the user's Startup folder. **No registry keys are
written anywhere in this application** - the shortcut is visible in Explorer and
can be deleted by hand, which makes the feature easy to audit and undo.

Disabled by default; nothing is written until the user turns the setting on.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Claude Profiles.lnk"
_CREATE_NO_WINDOW = 0x08000000


def startup_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path | None:
    directory = startup_dir()
    return directory / SHORTCUT_NAME if directory else None


def _launch_target() -> tuple[str, str]:
    """``(target, arguments)`` for the shortcut.

    A frozen build points at the exe directly; running from source points at
    ``pythonw.exe -m claude_profiles`` so no console window appears.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    interpreter = Path(sys.executable)
    windowed = interpreter.with_name("pythonw.exe")
    target = str(windowed if windowed.is_file() else interpreter)
    return target, "-m claude_profiles"


def is_enabled() -> bool:
    path = shortcut_path()
    return bool(path and path.is_file())


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Create or remove the Startup shortcut. Returns ``(ok, message)``."""
    path = shortcut_path()
    if path is None:
        return False, "Could not locate the Windows Startup folder."

    if not enabled:
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            return False, f"Could not remove the startup shortcut: {type(exc).__name__}"
        return True, "Removed from Windows startup."

    if sys.platform != "win32":
        return False, "Startup shortcuts are only supported on Windows."

    target, arguments = _launch_target()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not open the Startup folder: {type(exc).__name__}"

    # WScript.Shell is the standard, dependency-free way to author a .lnk.
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CP_LINK); "
        "$s.TargetPath = $env:CP_TARGET; "
        "$s.Arguments = $env:CP_ARGS; "
        "$s.WorkingDirectory = Split-Path -Parent $env:CP_TARGET; "
        "$s.Description = 'Claude Profiles'; "
        "$s.Save()"
    )
    env = dict(os.environ)
    env.update({"CP_LINK": str(path), "CP_TARGET": target, "CP_ARGS": arguments})
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not create the startup shortcut: {type(exc).__name__}"

    if completed.returncode != 0 or not path.is_file():
        return False, "Could not create the startup shortcut."
    return True, "Claude Profiles will start when you sign in."
