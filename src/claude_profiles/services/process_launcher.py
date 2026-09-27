"""Opens a visible terminal running ``cswap run <n>``.

This is separate from :mod:`cswap_client` because the goal is the opposite: the
client captures output into a pipe, whereas a launch must hand the user an
interactive console they can type into. Nothing here reads the child's output,
so no credential material can pass through this module.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CREATE_NEW_CONSOLE = 0x00000010


@dataclass(frozen=True)
class LaunchResult:
    ok: bool
    message: str
    used_windows_terminal: bool = False


def find_windows_terminal() -> str | None:
    found = shutil.which("wt")
    if found:
        return found
    candidate = (
        Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "wt.exe"
    )
    return str(candidate) if candidate.is_file() else None


def find_powershell() -> str | None:
    # PowerShell 7 if present, else Windows PowerShell, which always is.
    return shutil.which("pwsh") or shutil.which("powershell")


def find_claude() -> str | None:
    """Locate the Claude Code CLI, used only to open an interactive sign-in.

    Sign-in is an interactive OAuth flow that belongs in a real terminal, so the
    app opens one and steps back rather than trying to drive it.
    """
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def _quote_for_powershell(value: str) -> str:
    """Single-quote a path for PowerShell, escaping embedded quotes."""
    return "'" + value.replace("'", "''") + "'"


def build_powershell_command(argv: list[str], *, keep_open: bool = True) -> str:
    """``& 'C:\\path\\cswap.exe' run 2`` - the call operator handles spaces.

    With ``keep_open`` False the window closes once the command succeeds but
    stays put on failure, so an error is still readable. That suits a one-shot
    task such as signing in; an interactive Claude Code session wants its
    window to remain regardless.
    """
    if not argv:
        raise ValueError("empty command")
    executable, *args = argv
    parts = [_quote_for_powershell(executable), *(_quote_for_powershell(a) for a in args)]
    command = "& " + " ".join(parts)
    if keep_open:
        return command
    # Statements are separated by a newline, not a semicolon. Windows Terminal
    # treats an unescaped ";" in its command line as a subcommand separator, so
    # a semicolon here silently truncates the command at the "&" call and tries
    # to parse the rest as another wt subcommand. PowerShell accepts a newline
    # as a statement separator and wt passes it through untouched.
    pause = "if ($LASTEXITCODE -ne 0) { Read-Host 'Command failed - press Enter to close' }"
    return f"{command}\n{pause}"


class ProcessLauncher:
    """Spawns an interactive terminal for a per-account Claude Code session."""

    def __init__(
        self,
        *,
        terminal: str | None = None,
        shell: str | None = None,
        discover: bool = True,
        spawn=subprocess.Popen,
    ) -> None:
        # ``discover`` separates "not specified, go look" from "deliberately
        # absent", so the no-terminal fallback path can actually be exercised.
        self._terminal = terminal or (find_windows_terminal() if discover else None)
        self._shell = shell or (find_powershell() if discover else None)
        self._spawn = spawn

    @property
    def has_terminal(self) -> bool:
        return self._shell is not None

    def build_argv(
        self, command: list[str], title: str, *, keep_open: bool = True
    ) -> tuple[list[str], bool]:
        """Return ``(argv, used_windows_terminal)`` for the launch."""
        if self._shell is None:
            raise RuntimeError("No PowerShell executable was found.")
        inner = [self._shell]
        if keep_open:
            # An interactive session owns the window for as long as it runs.
            inner.append("-NoExit")
        inner += ["-Command", build_powershell_command(command, keep_open=keep_open)]
        if self._terminal:
            safe_title = title.replace('"', "").replace("\\", "")[:60]
            return ([self._terminal, "new-tab", "--title", safe_title, "--", *inner], True)
        return (inner, False)

    def launch(
        self, command: list[str], title: str, *, keep_open: bool = True
    ) -> LaunchResult:
        """Open a new terminal window running ``command``."""
        try:
            argv, used_wt = self.build_argv(command, title, keep_open=keep_open)
        except RuntimeError as exc:
            return LaunchResult(False, str(exc))

        kwargs = {}
        if sys.platform == "win32" and not used_wt:
            # Windows Terminal makes its own window; a bare shell needs one.
            kwargs["creationflags"] = CREATE_NEW_CONSOLE
        try:
            self._spawn(argv, **kwargs)
        except (OSError, ValueError) as exc:
            return LaunchResult(False, f"Could not open a terminal window: {type(exc).__name__}")
        return LaunchResult(True, f"Opened a terminal for {title}.", used_wt)
