"""Tests for the global shortcut manager.

Win32 registration was at 42% coverage and entirely unexercised. A probe
confirmed the real round trip works - a posted WM_HOTKEY reaches the callback,
and RegisterHotKey/UnregisterHotKey succeed - so these tests pin that behaviour
down and cover the failure paths, which are the ones a user actually hits when
another application already owns Ctrl+Alt+1.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

from claude_profiles.services import hotkeys
from claude_profiles.services.hotkeys import (
    DEFAULT_BINDINGS,
    WM_HOTKEY,
    HotkeyFilter,
    HotkeyManager,
)

pytestmark = pytest.mark.usefixtures("qapp")


class FakeUser32:
    """Records registrations and can refuse chosen hotkey ids."""

    def __init__(self, refuse: set[int] | None = None) -> None:
        self.refuse = refuse or set()
        self.registered: list[int] = []
        self.unregistered: list[int] = []

    def RegisterHotKey(self, hwnd, hotkey_id, modifiers, vk):  # noqa: N802
        if hotkey_id in self.refuse:
            return 0
        self.registered.append(hotkey_id)
        return 1

    def UnregisterHotKey(self, hwnd, hotkey_id):  # noqa: N802
        self.unregistered.append(hotkey_id)
        return 1


class FakeWindll:
    def __init__(self, user32: FakeUser32) -> None:
        self.user32 = user32


class FakeApp:
    def __init__(self) -> None:
        self.filters: list[object] = []

    def installNativeEventFilter(self, f):  # noqa: N802 - Qt naming
        self.filters.append(f)

    def removeNativeEventFilter(self, f):  # noqa: N802 - Qt naming
        if f in self.filters:
            self.filters.remove(f)


@pytest.fixture
def fake_win(monkeypatch):
    def _install(refuse: set[int] | None = None) -> FakeUser32:
        user32 = FakeUser32(refuse)
        monkeypatch.setattr(hotkeys.ctypes, "windll", FakeWindll(user32))
        monkeypatch.setattr(hotkeys.sys, "platform", "win32")
        return user32

    return _install


# --- registration ---------------------------------------------------------


def test_enabling_registers_every_binding(fake_win):
    user32 = fake_win()
    app = FakeApp()
    manager = HotkeyManager(lambda key: None)

    ok, message = manager.enable(app)

    assert ok is True
    assert manager.is_active is True
    assert len(user32.registered) == len(DEFAULT_BINDINGS)
    assert len(app.filters) == 1
    assert "Ctrl+Alt+1" in message


def test_disabling_releases_everything(fake_win):
    user32 = fake_win()
    app = FakeApp()
    manager = HotkeyManager(lambda key: None)
    manager.enable(app)

    manager.disable(app)

    assert manager.is_active is False
    assert len(user32.unregistered) == len(DEFAULT_BINDINGS)
    assert app.filters == [], "the event filter must be removed too"


def test_a_combination_already_in_use_is_reported_not_silently_dropped(fake_win):
    """Ctrl+Alt+1 is a popular shortcut; the user needs to be told."""
    fake_win(refuse={1})
    app = FakeApp()
    manager = HotkeyManager(lambda key: None)

    ok, message = manager.enable(app)

    assert ok is True, "one working shortcut is still worth having"
    assert manager.is_active is True
    assert "Ctrl+Alt+1" in message and "in use" in message


def test_all_combinations_refused_reports_failure_and_cleans_up(fake_win):
    fake_win(refuse={1, 2})
    app = FakeApp()
    manager = HotkeyManager(lambda key: None)

    ok, message = manager.enable(app)

    assert ok is False
    assert manager.is_active is False
    assert app.filters == [], "a failed enable must not leave a filter installed"
    assert "already using it" in message


def test_enabling_twice_does_not_install_a_second_filter(fake_win):
    fake_win()
    app = FakeApp()
    manager = HotkeyManager(lambda key: None)
    manager.enable(app)

    ok, message = manager.enable(app)

    assert ok is True
    assert len(app.filters) == 1, "a duplicate filter would double every trigger"
    assert "already active" in message


def test_disabling_when_never_enabled_is_harmless(fake_win):
    fake_win()
    manager = HotkeyManager(lambda key: None)
    manager.disable(FakeApp())
    assert manager.is_active is False


def test_unsupported_platform_declines_cleanly(monkeypatch):
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    manager = HotkeyManager(lambda key: None)

    ok, message = manager.enable(FakeApp())

    assert ok is False
    assert "only supported on Windows" in message


# --- dispatch -------------------------------------------------------------


def test_a_hotkey_id_maps_to_its_profile(fake_win):
    fake_win()
    app = FakeApp()
    fired: list[str] = []
    manager = HotkeyManager(fired.append)
    manager.enable(app)

    manager._handle(1)
    manager._handle(2)

    assert fired == [binding[0] for binding in DEFAULT_BINDINGS]


def test_an_unknown_hotkey_id_is_ignored(fake_win):
    fake_win()
    fired: list[str] = []
    manager = HotkeyManager(fired.append)
    manager.enable(FakeApp())

    manager._handle(99)

    assert fired == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 message structures")
def test_the_filter_decodes_a_real_wm_hotkey():
    """Verified against a genuine posted message, not a stand-in.

    The decode is wrapped in a bare except that returns quietly, so a change in
    what Qt hands over would disable every shortcut with no error anywhere.
    """
    from ctypes import wintypes

    fired: list[int] = []
    flt = HotkeyFilter(fired.append)

    msg = wintypes.MSG()
    msg.message = WM_HOTKEY
    msg.wParam = 2

    handled, _ = flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))

    assert handled is False, "the message must be passed along, not swallowed"
    assert fired == [2]


def test_the_filter_ignores_other_message_types():
    fired: list[int] = []
    flt = HotkeyFilter(fired.append)

    handled, _ = flt.nativeEventFilter(b"xcb_generic_event_t", 0)

    assert handled is False
    assert fired == []


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 message structures")
def test_an_unrelated_windows_message_does_not_trigger():
    from ctypes import wintypes

    fired: list[int] = []
    flt = HotkeyFilter(fired.append)

    msg = wintypes.MSG()
    msg.message = 0x0200  # WM_MOUSEMOVE
    msg.wParam = 1

    flt.nativeEventFilter(b"windows_generic_MSG", ctypes.addressof(msg))

    assert fired == []
