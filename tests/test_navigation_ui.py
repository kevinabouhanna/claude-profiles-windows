"""Tests for the navigation layout, the settings controls, and the pages."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from claude_profiles.main_window import (
    TAB_ACCOUNTS,
    TAB_ACTIVITY,
    TAB_DASHBOARD,
    TAB_PRIVACY,
    TAB_SETTINGS,
    MainWindow,
)
from claude_profiles.models import ActivityEntry
from claude_profiles.services.mock_cswap import MockCswapClient
from claude_profiles.services.profile_service import ProfileService
from claude_profiles.services.settings_service import Settings, SettingsService
from claude_profiles.widgets.controls import Stepper, ToggleSwitch, format_interval
from claude_profiles.widgets.navigation import NavigationPane
from claude_profiles.widgets.settings_card import SettingsCard

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def window(qtbot, tmp_path):
    def _make(settings: Settings | None = None) -> MainWindow:
        service = ProfileService(MockCswapClient("healthy"), SettingsService(tmp_path / "d"))
        w = MainWindow(service.states, settings or Settings(), data_dir=str(tmp_path))
        qtbot.addWidget(w)
        return w

    return _make


# --- toggle switch --------------------------------------------------------


def test_toggle_switch_flips_on_click(qtbot):
    toggle = ToggleSwitch()
    qtbot.addWidget(toggle)
    seen: list[bool] = []
    toggle.toggled.connect(seen.append)

    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)

    assert toggle.isChecked() is True
    assert seen == [True]


def test_toggle_switch_responds_to_the_keyboard(qtbot):
    toggle = ToggleSwitch()
    qtbot.addWidget(toggle)
    toggle.show()
    toggle.setFocus()
    qtbot.keyClick(toggle, Qt.Key.Key_Space)
    assert toggle.isChecked() is True


def test_a_disabled_toggle_ignores_clicks(qtbot):
    toggle = ToggleSwitch()
    qtbot.addWidget(toggle)
    toggle.setEnabled(False)
    qtbot.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert toggle.isChecked() is False


def test_a_hidden_toggle_jumps_rather_than_animating():
    """Loading settings before the window is shown must not leave it mid-slide."""
    toggle = ToggleSwitch()
    toggle.setChecked(True)
    assert toggle._position == 1.0


# --- stepper --------------------------------------------------------------


def test_stepper_moves_by_its_step(qtbot):
    stepper = Stepper(80, minimum=5, maximum=100, step=5)
    qtbot.addWidget(stepper)
    stepper.step_up()
    assert stepper.value() == 85
    stepper.step_down()
    stepper.step_down()
    assert stepper.value() == 75


def test_an_off_grid_value_snaps_on_the_next_press(qtbot):
    """83 from a hand-edited file is shown as-is, then snaps to 85 or 80."""
    stepper = Stepper(83, minimum=5, maximum=100, step=5)
    qtbot.addWidget(stepper)
    assert stepper.value() == 83
    stepper.step_up()
    assert stepper.value() == 85

    stepper.setValue(83)
    stepper.step_down()
    assert stepper.value() == 80


def test_stepper_walks_a_list_of_choices(qtbot):
    stepper = Stepper(120, choices=(30, 60, 120, 300), formatter=format_interval)
    qtbot.addWidget(stepper)
    stepper.step_up()
    assert stepper.value() == 300
    stepper.step_up()
    assert stepper.value() == 300, "stays at the last choice"
    assert stepper._label.text() == "5 min"


def test_stepper_buttons_disable_at_the_bounds(qtbot):
    stepper = Stepper(100, minimum=5, maximum=100, step=5)
    qtbot.addWidget(stepper)
    assert stepper._up.isEnabled() is False
    assert stepper._down.isEnabled() is True


def test_stepper_only_signals_real_changes(qtbot):
    stepper = Stepper(50, minimum=0, maximum=100, step=5)
    qtbot.addWidget(stepper)
    seen: list[int] = []
    stepper.valueChanged.connect(seen.append)

    stepper.setValue(50)
    stepper.setValue(55)

    assert seen == [55]


def test_stepper_keyboard(qtbot):
    stepper = Stepper(50, minimum=0, maximum=100, step=5)
    qtbot.addWidget(stepper)
    stepper.show()
    stepper.setFocus()
    qtbot.keyClick(stepper, Qt.Key.Key_Up)
    assert stepper.value() == 55
    qtbot.keyClick(stepper, Qt.Key.Key_Down)
    assert stepper.value() == 50


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(30, "30 s"), (60, "1 min"), (120, "2 min"), (900, "15 min"), (3600, "1 hour")],
)
def test_interval_formatting(seconds, text):
    assert format_interval(seconds) == text


# --- navigation pane ------------------------------------------------------


def test_navigation_reports_the_selected_page(qtbot):
    pane = NavigationPane()
    qtbot.addWidget(pane)
    first = pane.add_item("home", "One")
    second = pane.add_item("settings", "Two", footer=True)
    seen: list[int] = []
    pane.currentChanged.connect(seen.append)

    pane.set_current(first)
    qtbot.mouseClick(pane.item(second), Qt.MouseButton.LeftButton)

    assert seen == [first, second]
    assert pane.current_index == second
    assert pane.item(second).isChecked() and not pane.item(first).isChecked()


def test_reselecting_the_current_page_does_not_signal(qtbot):
    pane = NavigationPane()
    qtbot.addWidget(pane)
    index = pane.add_item("home", "One")
    pane.set_current(index)
    seen: list[int] = []
    pane.currentChanged.connect(seen.append)

    pane.set_current(index)

    assert seen == []


# --- the window -----------------------------------------------------------


def test_pages_are_grouped_as_documented(window):
    """Things you came to do on top; things about the app pinned below."""
    w = window()
    labels = [w._nav.item(i).text() for i in range(w._nav.count())]
    assert labels == ["Overview", "Accounts", "Activity", "Privacy", "Settings"]
    assert (TAB_DASHBOARD, TAB_ACCOUNTS, TAB_ACTIVITY, TAB_PRIVACY, TAB_SETTINGS) == (0, 1, 2, 3, 4)
    top_layout = w._nav._top
    footer_layout = w._nav._footer
    assert top_layout.count() == 3
    assert footer_layout.count() == 2


def test_selecting_a_page_shows_it(window):
    w = window()
    for index in (TAB_SETTINGS, TAB_PRIVACY, TAB_ACTIVITY, TAB_DASHBOARD):
        w.show_tab(index)
        assert w._stack.currentIndex() == index
        assert w.current_tab() == index


def test_clicking_accounts_in_the_pane_asks_for_a_refresh(window, qtbot):
    w = window()
    seen: list[int] = []
    w.accountsTabShown.connect(lambda: seen.append(1))
    qtbot.mouseClick(w._nav.item(TAB_ACCOUNTS), Qt.MouseButton.LeftButton)
    assert seen == [1]


def test_reopening_accounts_still_refreshes_it(window):
    w = window()
    w.show_tab(TAB_ACCOUNTS)
    seen: list[int] = []
    w.accountsTabShown.connect(lambda: seen.append(1))
    w.show_tab(TAB_ACCOUNTS)
    assert seen == [1]


def test_settings_are_grouped_into_named_sections(window):
    from claude_profiles.widgets.settings_card import SectionHeader

    w = window()
    page = w._stack.widget(TAB_SETTINGS)
    sections = [s.text() for s in page.findChildren(SectionHeader)]
    assert sections == ["General", "Notifications", "Keyboard shortcuts", "About"]


def test_every_setting_is_a_card_with_a_title_and_description(window):
    w = window()
    page = w._stack.widget(TAB_SETTINGS)
    cards = page.findChildren(SettingsCard)
    assert len(cards) >= 6
    for card in cards:
        assert card.title()
        assert card.description()


def test_thresholds_follow_the_alerts_switch(window):
    w = window(Settings(notifications_enabled=False))
    assert w._warn_stepper.isEnabled() is False
    assert w._critical_stepper.isEnabled() is False

    w._alerts_toggle.setChecked(True)

    assert w._warn_stepper.isEnabled() is True
    assert w._critical_stepper.isEnabled() is True


def test_the_warning_can_never_reach_the_critical_level(window):
    w = window(Settings(warn_threshold_pct=80, critical_threshold_pct=95))
    for _ in range(10):
        w._warn_stepper.step_up()
    assert w._warn_stepper.value() < w._critical_stepper.value()

    for _ in range(10):
        w._critical_stepper.step_down()
    assert w._warn_stepper.value() < w._critical_stepper.value()


def test_each_control_emits_the_full_settings(window):
    stored = Settings(autostart_target="abc", profile_aliases={"personal": "home", "work": "w"})
    w = window(stored)
    emitted: list[Settings] = []
    w.settingsChanged.connect(emitted.append)

    w._autostart_toggle.setChecked(False)
    w._hotkeys_toggle.setChecked(True)
    w._interval_stepper.step_up()

    latest = emitted[-1]
    assert latest.launch_at_signin is False
    assert latest.hotkeys_enabled is True
    assert latest.refresh_interval_seconds == 300
    assert latest.autostart_target == "abc"
    assert latest.profile_aliases == {"personal": "home", "work": "w"}


def test_loading_settings_does_not_echo_them_back(window):
    w = window()
    emitted: list[Settings] = []
    w.settingsChanged.connect(emitted.append)

    w.load_settings(
        Settings(refresh_interval_seconds=600, notifications_enabled=True,
                 warn_threshold_pct=60, critical_threshold_pct=90, hotkeys_enabled=True)
    )

    assert emitted == []
    assert w._interval_stepper.value() == 600
    assert w._warn_stepper.value() == 60
    assert w._critical_stepper.value() == 90
    assert w._hotkeys_toggle.isChecked() is True


def test_a_failed_shortcut_is_shown_on_its_card(window):
    w = window()
    w.set_hotkey_status("Could not register Ctrl+Alt+1.", ok=False)
    assert "Could not register" in w._hotkeys_card.status()
    assert w._hotkeys_card._status_tone == "critical"


def test_privacy_keeps_the_required_actions(window, qtbot):
    """The brief requires both actions to be visible on the Privacy page."""
    w = window()
    page = w._stack.widget(TAB_PRIVACY)
    cards = {card.title(): card for card in page.findChildren(SettingsCard)}
    assert "Open data folder" in cards
    assert "Clear local activity history" in cards

    opened: list[int] = []
    cleared: list[int] = []
    w.openDataFolderRequested.connect(lambda: opened.append(1))
    w.clearHistoryRequested.connect(lambda: cleared.append(1))
    qtbot.mouseClick(cards["Open data folder"].control, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(cards["Clear local activity history"].control, Qt.MouseButton.LeftButton)

    assert opened == [1] and cleared == [1]


def test_activity_shows_an_empty_state_until_something_happens(window):
    from datetime import datetime

    # isHidden() rather than isVisibleTo(window): the Activity page is not the
    # stack's current page here, so every child reports invisible to the
    # window whatever its own state.
    w = window()
    w.set_activity([])
    assert not w._activity_empty.isHidden()
    assert w._activity_list.isHidden()

    w.append_activity(ActivityEntry(timestamp=datetime.now(), message="Usage refreshed"))
    assert w._activity_empty.isHidden()
    assert not w._activity_list.isHidden()
    assert w._activity_list.count() == 1


def test_a_banner_can_carry_a_severity(window):
    w = window()
    w.show_banner("claude-swap reported an unfamiliar format.", "caution")
    assert w._banner.isVisibleTo(w)
    assert "unfamiliar" in w._banner.message()


def test_the_shortcut_description_covers_every_position(window):
    w = window()
    description = w._hotkeys_card.description()
    # Shortcuts follow account order, so they work for any number of accounts.
    assert "Ctrl+Alt+1" in description
    assert "Ctrl+Alt+9" in description
