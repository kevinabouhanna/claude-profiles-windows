"""Windows integration: start at sign-in, and a Start menu entry.

Both are shortcuts, deliberately. **No registry keys are written anywhere in
this application.** A ``.lnk`` is visible in Explorer, can be inspected, and
can be deleted by hand, which makes the behaviour easy to audit and to undo.

Shortcuts point at ``pythonw.exe`` (or the frozen exe) so launching the app
never opens a console window - it behaves like any other installed desktop
application.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Claude Profiles.lnk"
APP_NAME = "Claude Profiles"
_CREATE_NO_WINDOW = 0x08000000


# -- locations -------------------------------------------------------------


def startup_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def start_menu_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def shortcut_path() -> Path | None:
    directory = startup_dir()
    return directory / SHORTCUT_NAME if directory else None


def start_menu_path() -> Path | None:
    directory = start_menu_dir()
    return directory / SHORTCUT_NAME if directory else None


# -- launch target ---------------------------------------------------------


def launch_target() -> tuple[str, str]:
    """``(target, arguments)`` for a shortcut.

    A frozen build points at the exe. Running from source points at
    ``pythonw.exe`` - the windowed interpreter - so no console window appears.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    interpreter = Path(sys.executable)
    windowed = interpreter.with_name("pythonw.exe")
    target = str(windowed if windowed.is_file() else interpreter)
    return target, "-m claude_profiles"


def icon_path() -> str:
    """Generate (once) an .ico beside the app's data so shortcuts have an icon."""
    from ..resources.icons import save_app_icon
    from .settings_service import default_data_dir

    target = default_data_dir() / "app.ico"
    if not target.is_file():
        try:
            save_app_icon(target)
        except (OSError, ValueError):
            return ""
    return str(target)


# -- shortcut creation -----------------------------------------------------


def _create_shortcut(
    link: Path, *, description: str = APP_NAME
) -> tuple[bool, str]:
    """Author a .lnk with WScript.Shell, the dependency-free Windows way."""
    if sys.platform != "win32":
        return False, "Shortcuts are only supported on Windows."

    target, arguments = launch_target()
    try:
        link.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Could not open {link.parent.name}: {type(exc).__name__}"

    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CP_LINK); "
        "$s.TargetPath = $env:CP_TARGET; "
        "$s.Arguments = $env:CP_ARGS; "
        "$s.WorkingDirectory = Split-Path -Parent $env:CP_TARGET; "
        "$s.Description = $env:CP_DESC; "
        "if ($env:CP_ICON) { $s.IconLocation = $env:CP_ICON }; "
        "$s.Save()"
    )
    env = dict(os.environ)
    env.update(
        {
            "CP_LINK": str(link),
            "CP_TARGET": target,
            "CP_ARGS": arguments,
            "CP_DESC": description,
            "CP_ICON": icon_path(),
        }
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            timeout=25,
            creationflags=_CREATE_NO_WINDOW,
            env=env,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"Could not create the shortcut: {type(exc).__name__}"

    if completed.returncode != 0 or not link.is_file():
        return False, "Could not create the shortcut."
    return True, "ok"


def _remove(link: Path) -> tuple[bool, str]:
    try:
        link.unlink(missing_ok=True)
    except OSError as exc:
        return False, f"Could not remove the shortcut: {type(exc).__name__}"
    return True, "ok"


# -- start at sign-in ------------------------------------------------------


def is_enabled() -> bool:
    path = shortcut_path()
    return bool(path and path.is_file())


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """Create or remove the Startup shortcut. Returns ``(ok, message)``."""
    path = shortcut_path()
    if path is None:
        return False, "Could not locate the Windows Startup folder."

    if not enabled:
        ok, message = _remove(path)
        return (True, "Removed from Windows startup.") if ok else (False, message)

    ok, message = _create_shortcut(path, description="Claude Profiles (starts at sign-in)")
    if not ok:
        return False, message
    return True, "Claude Profiles will start when you sign in to Windows."


def reconcile(desired: bool) -> tuple[bool, str] | None:
    """Make the filesystem match the setting.

    Called at startup so a default-on preference actually takes effect on a
    fresh install, and so a shortcut deleted by hand is not silently reported
    as still enabled. Returns None when nothing needed doing.
    """
    if is_enabled() == desired:
        return None
    return set_enabled(desired)


# -- Start menu entry ------------------------------------------------------


def has_start_menu_entry() -> bool:
    path = start_menu_path()
    return bool(path and path.is_file())


def ensure_start_menu_entry() -> tuple[bool, str] | None:
    """Put the app in the Start menu so it is launchable like any other app.

    Created once and then left alone; if the user deletes it, it is not
    recreated behind their back.
    """
    path = start_menu_path()
    if path is None or path.is_file():
        return None
    ok, message = _create_shortcut(path)
    if not ok:
        return False, message
    return True, "Added Claude Profiles to the Start menu."


def remove_start_menu_entry() -> tuple[bool, str]:
    path = start_menu_path()
    if path is None:
        return False, "Could not locate the Start menu folder."
    ok, message = _remove(path)
    return (True, "Removed from the Start menu.") if ok else (False, message)
