"""Windows integration: start at sign-in, and a Start menu entry.

Both are shortcuts, deliberately. **No registry keys are written anywhere in
this application.** A ``.lnk`` is visible in Explorer, can be inspected, and
can be deleted by hand, which makes the behaviour easy to audit and to undo.

Shortcuts point at ``pythonw.exe`` (or the frozen exe) so launching the app
never opens a console window - it behaves like any other installed desktop
application.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

SHORTCUT_NAME = "Claude Profiles.lnk"
APP_NAME = "Claude Profiles"
EXE_NAME = "ClaudeProfiles.exe"
_CREATE_NO_WINDOW = 0x08000000

# PE subsystem values. 2 is a windowed binary, 3 a console one.
_SUBSYSTEM_GUI = 2
_SUBSYSTEM_CONSOLE = 3


def is_gui_executable(path: str | Path) -> bool | None:
    """Read a PE header and report whether the binary is windowed.

    This matters because a name is not a promise: the ``pythonw.exe`` inside a
    uv-created virtualenv is a trampoline compiled for the *console* subsystem,
    so launching through it opens a terminal despite the "w". Returns None when
    the file cannot be parsed.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    try:
        pe_offset = int.from_bytes(data[0x3C:0x40], "little")
        if data[pe_offset : pe_offset + 4] != b"PE\0\0":
            return None
        subsystem = int.from_bytes(
            data[pe_offset + 24 + 68 : pe_offset + 24 + 70], "little"
        )
    except (IndexError, ValueError):
        return None
    if subsystem not in (_SUBSYSTEM_GUI, _SUBSYSTEM_CONSOLE):
        return None
    return subsystem == _SUBSYSTEM_GUI


def install_dir() -> Path | None:
    """Where a user-scoped Windows application installs itself."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    return Path(base) / "Programs" / APP_NAME


def installed_exe() -> Path | None:
    directory = install_dir()
    if directory is None:
        return None
    candidate = directory / EXE_NAME
    return candidate if candidate.is_file() else None


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

    Preference order, all chosen so the app starts without a console:

    1. This process, when it is already the frozen build.
    2. An installed ``ClaudeProfiles.exe``, so a shortcut made during a source
       run still points at the real application.
    3. A genuinely windowed interpreter - one whose PE header says so, not one
       merely named ``pythonw.exe``.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""

    exe = installed_exe()
    if exe is not None:
        return str(exe), ""

    interpreter = Path(sys.executable)
    windowed = interpreter.with_name("pythonw.exe")
    if windowed.is_file() and is_gui_executable(windowed):
        return str(windowed), "-m claude_profiles"
    # Fall back to the base interpreter's pythonw when the local one is a
    # console trampoline; failing that, accept the console rather than refuse
    # to create a shortcut at all.
    base = Path(getattr(sys, "_base_executable", sys.executable)).with_name("pythonw.exe")
    if base.is_file() and is_gui_executable(base):
        return str(base), "-m claude_profiles"
    return str(windowed if windowed.is_file() else interpreter), "-m claude_profiles"


def target_fingerprint(target: str | None = None) -> str:
    """A short hash identifying the launcher the shortcuts point at.

    Used instead of the path itself because the path embeds the Windows
    username and settings.json is documented as holding nothing
    user-identifying. A hash compares just as well for change detection.
    """
    value = target if target is not None else launch_target()[0]
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:16]


def icon_path(data_dir: Path | None = None) -> str:
    """Generate (once) an .ico beside the app's data so shortcuts have an icon.

    Honours an overridden data directory so a test or a build verification run
    does not write into the real user profile.
    """
    from ..resources.icons import save_app_icon
    from .settings_service import default_data_dir

    base = Path(data_dir) if data_dir else default_data_dir()
    target = base / "app.ico"
    if not target.is_file():
        try:
            save_app_icon(target)
        except (OSError, ValueError):
            return ""
        if not target.is_file():
            # QPixmap.save reports failure by returning False, not raising.
            return ""
    return str(target)


# -- shortcut creation -----------------------------------------------------


def _create_shortcut(
    link: Path, *, description: str = APP_NAME, data_dir: Path | None = None
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
            "CP_ICON": icon_path(data_dir),
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


def set_enabled(enabled: bool, data_dir: Path | None = None) -> tuple[bool, str]:
    """Create or remove the Startup shortcut. Returns ``(ok, message)``."""
    path = shortcut_path()
    if path is None:
        return False, "Could not locate the Windows Startup folder."

    if not enabled:
        ok, message = _remove(path)
        return (True, "Removed from Windows startup.") if ok else (False, message)

    ok, message = _create_shortcut(
        path, description="Claude Profiles (starts at sign-in)", data_dir=data_dir
    )
    if not ok:
        return False, message
    return True, "Claude Profiles will start when you sign in to Windows."


def reconcile(
    desired: bool, recorded_target: str = "", data_dir: Path | None = None
) -> tuple[bool, str] | None:
    """Make the filesystem match the setting.

    Called at startup so a default-on preference actually takes effect on a
    fresh install, so a shortcut deleted by hand is not silently reported as
    still enabled, and so a shortcut left pointing at an old launcher is
    rewritten once a better one exists. Returns None when nothing needs doing.
    """
    current = target_fingerprint()
    if desired:
        if is_enabled() and recorded_target == current:
            return None
        return set_enabled(True, data_dir)
    if is_enabled():
        return set_enabled(False, data_dir)
    return None


# -- Start menu entry ------------------------------------------------------


def has_start_menu_entry() -> bool:
    path = start_menu_path()
    return bool(path and path.is_file())


def ensure_start_menu_entry(
    recorded_target: str = "", data_dir: Path | None = None
) -> tuple[bool, str] | None:
    """Put the app in the Start menu so it is launchable like any other app.

    Created once and then left alone, except when the launch target changes -
    a Start menu entry pointing at a launcher that no longer exists is worse
    than none. If the user deletes the entry it is not recreated behind their
    back.
    """
    path = start_menu_path()
    if path is None:
        return None
    if not path.is_file():
        # Absent because the user removed it, or because this is a first run.
        # Only the first run should create one; recreating a deleted entry
        # overrides a deliberate choice, which is what the docs promise not to
        # do. A recorded target means the app has authored shortcuts before.
        if recorded_target:
            return None
    elif recorded_target == target_fingerprint() or not recorded_target:
        return None
    ok, message = _create_shortcut(path, data_dir=data_dir)
    if not ok:
        return False, message
    return True, "Added Claude Profiles to the Start menu."


def remove_start_menu_entry() -> tuple[bool, str]:
    path = start_menu_path()
    if path is None:
        return False, "Could not locate the Start menu folder."
    ok, message = _remove(path)
    return (True, "Removed from the Start menu.") if ok else (False, message)
