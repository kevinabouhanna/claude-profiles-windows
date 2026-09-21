"""Tests for terminal launching.

Command construction is worth testing directly: a quoting mistake here would be
passed to a shell interpreter, so the argv this module builds is checked exactly
rather than assumed.
"""

from __future__ import annotations

import pytest

from claude_profiles.services.process_launcher import (
    ProcessLauncher,
    build_powershell_command,
)


class FakeSpawn:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.error = error

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if self.error is not None:
            raise self.error
        return object()

    @property
    def last_argv(self) -> list[str]:
        return self.calls[-1][0]


# --- command construction -------------------------------------------------


def test_builds_call_operator_command():
    command = build_powershell_command([r"C:\tools\cswap.exe", "run", "2"])
    assert command == r"& 'C:\tools\cswap.exe' 'run' '2'"


def test_paths_with_spaces_are_quoted():
    command = build_powershell_command([r"C:\Program Files\cswap.exe", "run", "1"])
    assert r"'C:\Program Files\cswap.exe'" in command
    assert command.startswith("& ")


def test_embedded_single_quotes_are_escaped():
    """PowerShell escapes a quote by doubling it; anything less breaks out."""
    command = build_powershell_command([r"C:\it's\cswap.exe", "run", "1"])
    assert "'C:\\it''s\\cswap.exe'" in command


def test_empty_command_is_rejected():
    with pytest.raises(ValueError):
        build_powershell_command([])


# --- argv assembly --------------------------------------------------------


def test_windows_terminal_argv_uses_separator():
    launcher = ProcessLauncher(terminal=r"C:\wt.exe", shell=r"C:\powershell.exe")
    argv, used_wt = launcher.build_argv([r"C:\cswap.exe", "run", "2"], "Claude - Work")

    assert used_wt is True
    assert argv[:2] == [r"C:\wt.exe", "new-tab"]
    # "--" stops Windows Terminal parsing the rest as its own options.
    assert "--" in argv
    assert argv[argv.index("--") + 1] == r"C:\powershell.exe"
    assert "-NoExit" in argv  # a failure stays readable


def test_title_is_sanitised():
    launcher = ProcessLauncher(terminal=r"C:\wt.exe", shell=r"C:\powershell.exe")
    argv, _ = launcher.build_argv([r"C:\cswap.exe", "run", "1"], 'evil" --x \\ title')
    title = argv[argv.index("--title") + 1]
    assert '"' not in title
    assert "\\" not in title


def test_falls_back_to_bare_shell_without_windows_terminal():
    launcher = ProcessLauncher(terminal=None, shell=r"C:\powershell.exe", discover=False)
    argv, used_wt = launcher.build_argv([r"C:\cswap.exe", "run", "1"], "Claude - Personal")

    assert used_wt is False
    assert argv[0] == r"C:\powershell.exe"


def test_missing_shell_is_reported_not_raised():
    launcher = ProcessLauncher(terminal=None, shell=None, discover=False)
    assert launcher.has_terminal is False
    result = launcher.launch([r"C:\cswap.exe", "run", "1"], "Claude")
    assert result.ok is False
    assert "PowerShell" in result.message


# --- launching ------------------------------------------------------------


def test_launch_spawns_without_a_shell():
    spawn = FakeSpawn()
    launcher = ProcessLauncher(terminal=r"C:\wt.exe", shell=r"C:\powershell.exe", spawn=spawn)

    result = launcher.launch([r"C:\cswap.exe", "run", "2"], "Claude - Work")

    assert result.ok is True
    assert isinstance(spawn.last_argv, list)
    assert spawn.calls[-1][1].get("shell") in (None, False)


def test_bare_shell_launch_requests_a_new_console(monkeypatch):
    monkeypatch.setattr("claude_profiles.services.process_launcher.sys.platform", "win32")
    spawn = FakeSpawn()
    launcher = ProcessLauncher(
        terminal=None, shell=r"C:\powershell.exe", discover=False, spawn=spawn
    )

    launcher.launch([r"C:\cswap.exe", "run", "1"], "Claude")

    assert spawn.calls[-1][1]["creationflags"] == 0x00000010


def test_windows_terminal_launch_does_not_request_a_console(monkeypatch):
    monkeypatch.setattr("claude_profiles.services.process_launcher.sys.platform", "win32")
    spawn = FakeSpawn()
    launcher = ProcessLauncher(terminal=r"C:\wt.exe", shell=r"C:\powershell.exe", spawn=spawn)

    launcher.launch([r"C:\cswap.exe", "run", "1"], "Claude")

    assert "creationflags" not in spawn.calls[-1][1]


def test_spawn_failure_is_reported_safely():
    spawn = FakeSpawn(error=OSError("access is denied for C:\\secret\\path"))
    launcher = ProcessLauncher(
        terminal=None, shell=r"C:\powershell.exe", discover=False, spawn=spawn
    )

    result = launcher.launch([r"C:\cswap.exe", "run", "1"], "Claude")

    assert result.ok is False
    # The message names the failure type, not the underlying text.
    assert "OSError" in result.message
    assert "secret" not in result.message
