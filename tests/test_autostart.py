"""Tests for Windows startup integration.

These cover the decision logic and the launch target without authoring real
shortcuts; shortcut creation itself is a single PowerShell call and is verified
manually on Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from claude_profiles.services import autostart
from claude_profiles.services.settings_service import Settings


@pytest.fixture
def fake_dirs(tmp_path, monkeypatch):
    startup = tmp_path / "Startup"
    start_menu = tmp_path / "Programs"
    startup.mkdir()
    start_menu.mkdir()
    monkeypatch.setattr(autostart, "startup_dir", lambda: startup)
    monkeypatch.setattr(autostart, "start_menu_dir", lambda: start_menu)
    return startup, start_menu


@pytest.fixture
def fake_creator(monkeypatch):
    """Record shortcut writes instead of shelling out to PowerShell."""
    created: list[Path] = []

    def _create(link: Path, *, description: str = "") -> tuple[bool, str]:
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_text("shortcut", encoding="utf-8")
        created.append(link)
        return True, "ok"

    monkeypatch.setattr(autostart, "_create_shortcut", _create)
    return created


# --- default ---------------------------------------------------------------


def test_start_at_signin_is_on_by_default():
    assert Settings().launch_at_signin is True


def test_default_survives_normalisation():
    assert Settings().normalized().launch_at_signin is True


# --- enable / disable ------------------------------------------------------


def test_enabling_creates_the_startup_shortcut(fake_dirs, fake_creator):
    startup, _ = fake_dirs
    assert autostart.is_enabled() is False

    ok, message = autostart.set_enabled(True)

    assert ok is True
    assert (startup / autostart.SHORTCUT_NAME).is_file()
    assert autostart.is_enabled() is True
    assert "sign in" in message.lower()


def test_disabling_removes_the_shortcut(fake_dirs, fake_creator):
    startup, _ = fake_dirs
    autostart.set_enabled(True)

    ok, message = autostart.set_enabled(False)

    assert ok is True
    assert not (startup / autostart.SHORTCUT_NAME).exists()
    assert autostart.is_enabled() is False
    assert "Removed" in message


def test_disabling_when_absent_is_not_an_error(fake_dirs, fake_creator):
    assert autostart.set_enabled(False)[0] is True


def test_missing_startup_folder_is_reported(monkeypatch):
    monkeypatch.setattr(autostart, "startup_dir", lambda: None)
    ok, message = autostart.set_enabled(True)
    assert ok is False
    assert "Startup folder" in message


# --- reconcile -------------------------------------------------------------


def test_reconcile_creates_when_setting_is_on(fake_dirs, fake_creator):
    """A default-on preference must take effect on a fresh install."""
    startup, _ = fake_dirs
    result = autostart.reconcile(True)

    assert result is not None and result[0] is True
    assert (startup / autostart.SHORTCUT_NAME).is_file()


def test_reconcile_is_a_noop_when_already_correct(fake_dirs, fake_creator):
    autostart.set_enabled(True)
    fake_creator.clear()

    assert autostart.reconcile(True) is None
    assert fake_creator == []  # nothing rewritten


def test_reconcile_removes_when_setting_is_off(fake_dirs, fake_creator):
    startup, _ = fake_dirs
    autostart.set_enabled(True)

    result = autostart.reconcile(False)

    assert result is not None and result[0] is True
    assert not (startup / autostart.SHORTCUT_NAME).exists()


def test_reconcile_restores_a_hand_deleted_shortcut(fake_dirs, fake_creator):
    startup, _ = fake_dirs
    autostart.set_enabled(True)
    (startup / autostart.SHORTCUT_NAME).unlink()

    assert autostart.reconcile(True) is not None
    assert (startup / autostart.SHORTCUT_NAME).is_file()


# --- Start menu ------------------------------------------------------------


def test_start_menu_entry_is_created_once(fake_dirs, fake_creator):
    _, start_menu = fake_dirs
    result = autostart.ensure_start_menu_entry()

    assert result is not None and result[0] is True
    assert (start_menu / autostart.SHORTCUT_NAME).is_file()

    # A second run must not rewrite it.
    fake_creator.clear()
    assert autostart.ensure_start_menu_entry() is None
    assert fake_creator == []


def test_start_menu_entry_is_not_recreated_after_removal(fake_dirs, fake_creator):
    """Deleting it is a user decision, not drift to be corrected."""
    _, start_menu = fake_dirs
    autostart.ensure_start_menu_entry()
    autostart.remove_start_menu_entry()
    assert not (start_menu / autostart.SHORTCUT_NAME).exists()


# --- launch target ---------------------------------------------------------


def test_launch_target_avoids_a_console_window():
    """pythonw.exe is what keeps a terminal from appearing at sign-in."""
    target, arguments = autostart.launch_target()
    if not getattr(sys, "frozen", False):
        assert arguments == "-m claude_profiles"
        # On Windows the venv ships pythonw.exe beside python.exe.
        if (Path(sys.executable).with_name("pythonw.exe")).is_file():
            assert target.lower().endswith("pythonw.exe")


def test_frozen_build_points_at_the_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\ClaudeProfiles.exe")
    target, arguments = autostart.launch_target()
    assert target == r"C:\Apps\ClaudeProfiles.exe"
    assert arguments == ""
