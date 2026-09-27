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

    def _create(link: Path, *, description: str = "", data_dir=None) -> tuple[bool, str]:
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
    current = autostart.target_fingerprint()

    assert autostart.reconcile(True, current) is None
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
    """Deleting it is a user decision, not drift to be corrected.

    The point is what happens on the *next* start, so this has to call
    ensure_start_menu_entry() again - the earlier version stopped at asserting
    the file was gone and passed while the entry was being recreated.
    """
    _, start_menu = fake_dirs
    autostart.ensure_start_menu_entry()
    recorded = autostart.target_fingerprint()
    autostart.remove_start_menu_entry()
    assert not (start_menu / autostart.SHORTCUT_NAME).exists()

    fake_creator.clear()
    autostart.ensure_start_menu_entry(recorded)

    assert not (start_menu / autostart.SHORTCUT_NAME).exists(), (
        "a deleted Start menu entry must stay deleted"
    )
    assert fake_creator == []


# --- launch target ---------------------------------------------------------


def test_launch_target_never_picks_a_console_binary():
    """The point is a launcher that opens no terminal, whichever one is used.

    Asserting the *filename* was the original mistake: uv's venv pythonw.exe is
    a console-subsystem trampoline, so the name passed while the behaviour was
    wrong. The PE header is the thing worth checking.
    """
    target, arguments = autostart.launch_target()
    assert target

    windowed = autostart.is_gui_executable(target)
    if windowed is not None:
        assert windowed is True, f"{target} would open a console"

    # An exe launches itself; an interpreter needs the module argument.
    if target.lower().endswith(autostart.EXE_NAME.lower()):
        assert arguments == ""
    else:
        assert arguments == "-m claude_profiles"


def test_frozen_build_points_at_the_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\ClaudeProfiles.exe")
    target, arguments = autostart.launch_target()
    assert target == r"C:\Apps\ClaudeProfiles.exe"
    assert arguments == ""


# --- launcher changes ------------------------------------------------------


def test_reconcile_rewrites_a_shortcut_left_on_an_old_launcher(fake_dirs, fake_creator):
    """Installing the exe must repoint a shortcut made during a source run."""
    autostart.set_enabled(True)
    fake_creator.clear()

    result = autostart.reconcile(True, autostart.target_fingerprint(r"C:\old\pythonw.exe"))

    assert result is not None and result[0] is True
    assert fake_creator, "shortcut should have been rewritten"


def test_reconcile_leaves_a_matching_shortcut_alone(fake_dirs, fake_creator):
    autostart.set_enabled(True)
    fake_creator.clear()
    current = autostart.target_fingerprint()

    assert autostart.reconcile(True, current) is None
    assert fake_creator == []


def test_start_menu_entry_is_rewritten_when_the_target_changes(fake_dirs, fake_creator):
    autostart.ensure_start_menu_entry()
    fake_creator.clear()

    result = autostart.ensure_start_menu_entry(autostart.target_fingerprint(r"C:\old\pythonw.exe"))

    assert result is not None and result[0] is True
    assert fake_creator


def test_installed_exe_is_preferred_over_the_interpreter(tmp_path, monkeypatch):
    """A shortcut should point at the real app, not at a dev interpreter."""
    install = tmp_path / "Programs" / "Claude Profiles"
    install.mkdir(parents=True)
    exe = install / autostart.EXE_NAME
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(autostart, "install_dir", lambda: install)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    target, arguments = autostart.launch_target()

    assert target == str(exe)
    assert arguments == ""


def test_a_windowed_interpreter_is_preferred_over_a_trampoline(tmp_path, monkeypatch):
    """uv's venv pythonw.exe is console-subsystem despite the name.

    Asserting only that *something* was returned could not tell rejecting a
    trampoline from returning it, which is what the fallback does.
    """
    monkeypatch.setattr(autostart, "install_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    venv = tmp_path / "venv" / "Scripts"
    venv.mkdir(parents=True)
    trampoline = venv / "pythonw.exe"
    trampoline.write_bytes(b"MZ")
    base = tmp_path / "base"
    base.mkdir()
    windowed = base / "pythonw.exe"
    windowed.write_bytes(b"MZ")

    monkeypatch.setattr(sys, "executable", str(venv / "python.exe"))
    monkeypatch.setattr(sys, "_base_executable", str(base / "python.exe"), raising=False)
    # Only the base one is a real windowed binary.
    monkeypatch.setattr(
        autostart, "is_gui_executable", lambda p: Path(p) == windowed
    )

    target, arguments = autostart.launch_target()

    assert target == str(windowed), "must skip the console trampoline"
    assert arguments == "-m claude_profiles"


# --- settings file robustness ---------------------------------------------


def test_settings_with_a_byte_order_mark_still_load(tmp_path):
    """Notepad and PowerShell write a BOM; losing every setting to it is rude."""
    from claude_profiles.services.settings_service import SettingsService

    data_dir = tmp_path / "ClaudeProfiles"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(
        '﻿{"refresh_interval_seconds": 300, "notifications_enabled": true}',
        encoding="utf-8",
    )

    loaded = SettingsService(data_dir=data_dir).load()

    assert loaded.refresh_interval_seconds == 300
    assert loaded.notifications_enabled is True


def test_corrupt_settings_fall_back_to_defaults(tmp_path):
    from claude_profiles.services.settings_service import SettingsService

    data_dir = tmp_path / "ClaudeProfiles"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text("{not json at all", encoding="utf-8")

    loaded = SettingsService(data_dir=data_dir).load()

    assert loaded.refresh_interval_seconds == 120
    assert loaded.launch_at_signin is True


# --- finding 10: the icon ignored an overridden data directory ------------


def test_icon_is_written_to_the_given_data_directory(tmp_path, monkeypatch, qapp):
    """--data-dir exists so a test or build check stays out of the real profile."""
    from claude_profiles.services import settings_service

    real = tmp_path / "real"
    override = tmp_path / "override"
    override.mkdir(parents=True)
    monkeypatch.setattr(settings_service, "default_data_dir", lambda: real)

    written = autostart.icon_path(override)

    assert written, "an icon should have been produced"
    assert Path(written).parent == override
    assert not real.exists(), "the real data folder must not be touched"


def test_icon_path_reports_failure_rather_than_a_missing_file(
    tmp_path, monkeypatch, qapp
):
    """QPixmap.save signals failure by returning False, not by raising."""
    from claude_profiles.resources import icons

    monkeypatch.setattr(icons, "save_app_icon", lambda p, **k: p)  # writes nothing
    assert autostart.icon_path(tmp_path) == ""


def test_icon_path_refuses_to_draw_without_a_qt_application(tmp_path, monkeypatch):
    """Constructing a QPixmap with no QGuiApplication aborts the process.

    It is not a catchable exception - the interpreter dies with no traceback,
    which is how a PyInstaller build once appeared to crash at the PYZ stage
    for no reason. Returning "" gives a shortcut without an icon instead.
    """
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "instance", staticmethod(lambda: None))

    assert autostart.icon_path(tmp_path) == ""
    assert not (tmp_path / "app.ico").exists()


# --- finding 12: the recorded launcher embedded the Windows username ------


def test_the_recorded_launcher_is_a_hash_not_a_path():
    """settings.json is documented as holding nothing user-identifying."""
    target = r"C:\Users\somebody\AppData\Local\Programs\Claude Profiles\App.exe"
    fingerprint = autostart.target_fingerprint(target)

    assert len(fingerprint) == 16
    assert all(c in "0123456789abcdef" for c in fingerprint)
    assert "somebody" not in fingerprint
    assert "\\" not in fingerprint and "/" not in fingerprint


def test_the_fingerprint_is_stable_and_distinguishes_launchers():
    a = autostart.target_fingerprint(r"C:\one\app.exe")
    b = autostart.target_fingerprint(r"C:\two\app.exe")

    assert a == autostart.target_fingerprint(r"C:\one\app.exe")
    assert a != b, "a changed launcher must be detectable"
