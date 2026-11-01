"""Regression tests for the high-severity audit findings.

Each fix here was made without a test guarding it, which is the same gap that
let the earlier bugs recur: the fix is correct today and nothing stops it being
undone tomorrow. Every test below is written to fail against the pre-fix
behaviour, not merely to pass against the current code.
"""

from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QPushButton, QSystemTrayIcon, QWidget

from claude_profiles.main_window import MainWindow
from claude_profiles.services import autostart
from claude_profiles.services.mock_cswap import MockCswapClient
from claude_profiles.services.profile_service import ProfileService
from claude_profiles.services.settings_service import Settings, SettingsService
from claude_profiles.tray import TrayController
from claude_profiles.widgets import compact_popup

pytestmark = pytest.mark.usefixtures("qapp")


@pytest.fixture
def controller(qapp, tmp_path, qtbot):
    """A real TrayController with Windows integration disabled."""
    created: list[TrayController] = []

    def _make(scenario: str = "healthy") -> TrayController:
        service = SettingsService(data_dir=tmp_path / "data")
        ctl = TrayController(
            qapp,
            MockCswapClient(scenario),
            service,
            mock_mode=True,
            skip_windows_integration=True,
        )
        created.append(ctl)
        return ctl

    yield _make

    for ctl in created:
        ctl.polling.stop()
        ctl.popup.hide()
        if ctl._window is not None:
            ctl._window.hide()


# --- finding 3: settings edits wiped fields the page does not show --------


def test_editing_a_setting_preserves_fields_the_page_does_not_show(tmp_path, qtbot):
    """The Settings page owns six fields; it must not reset the others.

    Rebuilding Settings() from the widgets dropped autostart_target and
    profile_aliases, so every edit re-triggered shortcut authoring on the next
    launch and discarded any alias customisation.
    """
    stored = Settings(
        refresh_interval_seconds=120,
        autostart_target="abc123def456",
        profile_aliases={"personal": "home", "work": "office"},
    )
    window = MainWindow(
        tuple(ProfileService(MockCswapClient(), SettingsService(tmp_path)).states),
        stored,
        data_dir=str(tmp_path),
    )
    qtbot.addWidget(window)

    emitted: list[Settings] = []
    window.settingsChanged.connect(emitted.append)

    window._interval_stepper.setValue(300)

    assert emitted, "changing the interval should emit"
    latest = emitted[-1]
    assert latest.refresh_interval_seconds == 300
    assert latest.autostart_target == "abc123def456", "hash was reset"
    assert latest.profile_aliases == {"personal": "home", "work": "office"}


# --- finding 7: a minimized window still counted as visible ---------------


def test_a_minimized_window_does_not_hold_the_fast_cadence(controller):
    """QWidget.isVisible() stays True when minimized.

    Relying on it alone kept polling every 30s with nothing on screen, which is
    precisely the drain the adaptive cadence exists to avoid.
    """
    ctl = controller()
    window = ctl._ensure_window()
    window.show()
    assert ctl._ui_visible() is True
    assert ctl._current_interval() == 30.0

    window.showMinimized()

    assert ctl._ui_visible() is False, "a minimized window is not on screen"
    assert ctl._current_interval() == float(ctl._settings.refresh_interval_seconds)
    window.hide()


def test_hiding_everything_restores_the_configured_interval(controller):
    ctl = controller()
    ctl.popup.show()
    assert ctl._current_interval() == 30.0

    ctl.popup.hide()

    assert ctl._current_interval() == 120.0


# --- finding 5: a transient failure disabled the preference for good ------


def test_a_failed_shortcut_write_keeps_the_preference(controller, monkeypatch):
    """Antivirus or a slow cold PowerShell must not become a permanent opt-out."""
    ctl = controller()
    ctl._skip_windows_integration = False
    monkeypatch.setattr(
        autostart, "reconcile", lambda *a, **k: (False, "Could not create the shortcut.")
    )
    monkeypatch.setattr(autostart, "ensure_start_menu_entry", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "is_enabled", lambda: False)

    ctl._sync_windows_integration()

    assert ctl._settings.launch_at_signin is True, "preference was silently turned off"
    assert ctl._settings_service.load().launch_at_signin is True


def test_a_successful_write_records_the_launcher(controller, monkeypatch):
    ctl = controller()
    ctl._skip_windows_integration = False
    monkeypatch.setattr(autostart, "reconcile", lambda *a, **k: (True, "done"))
    monkeypatch.setattr(autostart, "ensure_start_menu_entry", lambda *a, **k: None)

    ctl._sync_windows_integration()

    assert ctl._settings.autostart_target == autostart.target_fingerprint()


# --- finding 6: startup froze the GUI thread ------------------------------


def test_startup_does_not_block_on_windows_integration(controller, monkeypatch):
    """Two cold PowerShell calls inline froze the tray for seconds at sign-in."""
    ctl = controller()
    ctl._skip_windows_integration = False
    started = threading.Event()

    def slow_reconcile(*args, **kwargs):
        started.set()
        time.sleep(1.5)
        return None

    monkeypatch.setattr(autostart, "reconcile", slow_reconcile)
    monkeypatch.setattr(autostart, "ensure_start_menu_entry", lambda *a, **k: None)

    begin = time.monotonic()
    ctl.start()
    elapsed = time.monotonic() - begin

    assert started.wait(timeout=5), "integration never ran"
    assert elapsed < 0.5, f"start() blocked for {elapsed:.2f}s on the GUI thread"
    ctl.polling.stop()


# --- finding 13: a poll race left the UI stuck on "Refreshing..." ---------


def test_poll_now_claims_the_slot_before_announcing_a_start(controller, monkeypatch):
    """The claim must happen on the calling thread, not inside the worker.

    Deliberately deterministic: the worker is prevented from starting, which
    isolates the claim itself. With the claim inside the worker, both calls
    pass the in-flight check and announce a start, but only one can ever
    finish - so the status line stays on "Refreshing..." forever. Racing real
    threads does not reproduce this reliably, which is why an earlier version
    of this test passed against the broken code.
    """
    from claude_profiles.services import polling_service

    # Note: no polling.stop() here. stop() sets the shutdown flag, after
    # which poll_now refuses everything - which would make this test pass for
    # entirely the wrong reason. The fixture never starts the loop.
    ctl = controller()

    class DormantThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(polling_service.threading, "Thread", DormantThread)

    starts: list[int] = []
    ctl.polling.pollStarted.connect(lambda: starts.append(1))

    try:
        first = ctl.polling.poll_now()
        second = ctl.polling.poll_now()

        assert first is True
        assert second is False, "a second poll must be refused while one is claimed"
        assert len(starts) == 1, (
            f"{len(starts)} starts announced; every start needs a matching finish"
        )
    finally:
        ctl.polling.coordinator._end()


def test_a_second_poll_request_is_refused_while_one_runs(controller):
    ctl = controller()

    assert ctl.polling.coordinator.try_begin() is True
    try:
        assert ctl.polling.poll_now() is False, "claimed slot must refuse a second poll"
    finally:
        ctl.polling.coordinator._end()


# --- finding 8: false recovery when an account vanished -------------------


def test_an_account_disappearing_is_not_reported_as_a_recovery(tmp_path):
    """needs_reauth is also False when the account is simply gone."""
    settings = SettingsService(data_dir=tmp_path / "d")
    broken = MockCswapClient("work_reauth")
    empty = MockCswapClient("no_accounts")
    svc = ProfileService(broken, settings)

    svc.apply_accounts(broken.list_accounts())
    svc.apply_accounts(empty.list_accounts())

    messages = [e.message for e in svc.recent_activity()]
    assert not any("signed in again" in m for m in messages), (
        "announced a sign-in for an account that was removed"
    )


# --- the Accounts page must reflect reality -------------------------------


def test_accounts_page_populates_when_its_tab_is_selected(controller, qtbot):
    """Selecting the tab used to populate nothing.

    The page was only ever filled by the tray menu's "Set up accounts...",
    so opening the dashboard and clicking Accounts showed "Checking...",
    "Not set up" and "None yet" while both profiles were in fact registered.
    """
    from claude_profiles.main_window import TAB_ACCOUNTS, TAB_DASHBOARD

    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()
    window.set_accounts(ctl.profiles.last_accounts)
    window.update_states(ctl.profiles.states)
    window.show_tab(TAB_DASHBOARD)

    seen: list[int] = []
    window.accountsTabShown.connect(lambda: seen.append(1))

    window.show_tab(TAB_ACCOUNTS)

    assert seen, "selecting the Accounts tab must ask for a refresh"
    assert window.setup_page._accounts_label.text() != "None yet."


def test_a_refresh_keeps_the_accounts_page_in_step(controller):
    """update_states is the common path; the page must ride along with it."""
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()
    window.set_accounts(ctl.profiles.last_accounts)

    window.update_states(ctl.profiles.states)

    listed = window.setup_page._accounts_label.text()
    assert ctl.profiles.states, "the healthy demo has accounts"
    for state in ctl.profiles.states:
        assert state.account.email in listed


def test_a_configured_page_drops_the_step_numbering(controller):
    """Numbered steps imply unfinished setup."""
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()
    window.set_accounts(ctl.profiles.last_accounts)
    window.update_states(ctl.profiles.states)

    assert "Step 1" not in window.setup_page._signin_step.title()
    assert "Step 2" not in window.setup_page._register_step.title()
    assert "connected" in window.setup_page._summary.text()


def test_a_fresh_install_still_shows_numbered_steps(controller):
    ctl = controller("no_accounts")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()
    window.set_accounts(ctl.profiles.last_accounts)
    window.update_states(ctl.profiles.states)

    assert "Step 1" in window.setup_page._signin_step.title()
    assert "Step 2" in window.setup_page._register_step.title()


# --- opening the dashboard flashed a burst of blank windows ---------------


class _WindowSpy(QObject):
    """Records every top-level window that is shown, however briefly."""

    def __init__(self) -> None:
        super().__init__()
        self.shown: list[QWidget] = []

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow():
            self.shown.append(obj)
        return False


def test_opening_settings_shows_no_window_but_its_own(controller, qapp):
    """A widget made visible before it has a parent opens as a window.

    Settings cards showed their description label before adding it to the
    card, and badges showed themselves on construction, so the first open of
    the dashboard flashed one blank window per card - seen as "several
    terminals" opening and closing. Later opens reuse the built window, which
    is why it happened only the first time.
    """
    from claude_profiles.main_window import TAB_SETTINGS

    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    spy = _WindowSpy()
    qapp.installEventFilter(spy)
    try:
        ctl._show_window(tab=TAB_SETTINGS)
        qapp.processEvents()
    finally:
        qapp.removeEventFilter(spy)

    assert ctl.profiles.states, "the cards and their badges must have been built"
    assert spy.shown == [ctl._window]


# --- clicking the tray icon could not close the flyout --------------------


def test_clicking_the_tray_icon_again_closes_the_flyout(controller):
    """The press closes a Qt popup before the tray reports the click.

    So by the time the second click arrived the flyout was already hidden, and
    the handler, seeing it hidden, opened it again.
    """
    ctl = controller("healthy")
    trigger = QSystemTrayIcon.ActivationReason.Trigger

    ctl._on_tray_activated(trigger)
    assert ctl.popup.isVisible()

    ctl.popup.hide()  # what Qt does with the press on the tray icon
    ctl._on_tray_activated(trigger)
    assert not ctl.popup.isVisible()


def test_the_tray_icon_reopens_a_flyout_closed_earlier(controller):
    ctl = controller("healthy")
    trigger = QSystemTrayIcon.ActivationReason.Trigger
    ctl._on_tray_activated(trigger)
    ctl.popup.hide()
    ctl.popup._hidden_at -= compact_popup.REOPEN_GUARD_SECONDS  # time passes

    ctl._on_tray_activated(trigger)

    assert ctl.popup.isVisible()


def test_the_tray_icon_closes_a_flyout_that_is_still_open(controller):
    ctl = controller("healthy")
    trigger = QSystemTrayIcon.ActivationReason.Trigger
    ctl._on_tray_activated(trigger)

    ctl._on_tray_activated(trigger)

    assert not ctl.popup.isVisible()


# --- refresh is automatic, so there is no button for it -------------------


def test_there_is_no_refresh_button_anywhere(controller):
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()

    menu_items = [action.text() for action in ctl._menu.actions()]
    buttons = ctl.popup.findChildren(QPushButton) + window.findChildren(QPushButton)

    assert not [text for text in menu_items if "Refresh" in text]
    assert not [b for b in buttons if "Refresh" in b.accessibleName()]


# --- a fresh sign-in claude-swap had not stored ---------------------------


def _work_signed_in(ctl, *, stale: bool = False, dead: bool = False):
    from claude_profiles.services.mock_cswap import WORK_EMAIL

    backend = ctl._backend
    backend.switch("work")
    if stale:
        backend.stale_logins.add(WORK_EMAIL)
    if dead:
        backend.dead_logins.add(WORK_EMAIL)
    ctl.profiles.refresh_sync()
    assert ctl.profiles.state("work").needs_reauth
    backend.calls.clear()
    return backend


def test_the_poller_picks_up_a_fresh_sign_in_on_its_own(controller, qtbot):
    """Signing in with Claude Code must be all it takes: no button, no cswap add."""
    ctl = controller("two_accounts")
    _work_signed_in(ctl, stale=True)

    assert ctl.polling.poll_now()
    qtbot.waitUntil(lambda: not ctl.profiles.state("work").needs_reauth, timeout=5000)
    qtbot.waitUntil(
        lambda: any(
            "Picked up the new Claude Code sign-in for Work" in e.message
            for e in ctl.profiles.recent_activity()
        ),
        timeout=5000,
    )


def test_fix_sign_in_restores_the_active_login_without_a_dialog(controller, qtbot):
    ctl = controller("two_accounts")
    backend = _work_signed_in(ctl, stale=True)

    ctl._fix_sign_in("work")

    qtbot.waitUntil(lambda: not ctl.profiles.state("work").needs_reauth, timeout=5000)
    qtbot.waitUntil(lambda: not ctl.profiles.is_busy, timeout=5000)
    assert "resync" in backend.calls
    assert ctl._setup_dialog is None


def test_fix_sign_in_opens_the_reconnect_wizard_when_the_login_is_dead(controller, qtbot):
    ctl = controller("two_accounts")
    _work_signed_in(ctl, dead=True)

    ctl._fix_sign_in("work")

    qtbot.waitUntil(lambda: ctl._setup_dialog is not None, timeout=5000)
    dialog = ctl._setup_dialog
    assert dialog.profile is not None and dialog.profile.key == "work"
    assert "Sign in again" in dialog._result.text()
    dialog.reject()


def test_fix_sign_in_on_an_idle_account_goes_straight_to_the_wizard(controller, qtbot):
    """An account Claude Code is not signed in as has no login to pick up."""
    from claude_profiles.services.mock_cswap import WORK_EMAIL

    ctl = controller("two_accounts")
    backend = ctl._backend
    backend.stale_logins.add(WORK_EMAIL)
    ctl.profiles.refresh_sync()
    assert not ctl.profiles.state("work").is_active
    backend.calls.clear()

    ctl._fix_sign_in("work")

    dialog = ctl._setup_dialog
    assert dialog is not None and dialog.profile.key == "work"
    assert "resync" not in backend.calls
    dialog.reject()


# --- every button has a name a screen reader can say ---------------------


def _spoken_name(button: QPushButton) -> str:
    """What UI Automation reports: the accessible name, else the raw text."""
    return button.accessibleName() or button.text()


def test_every_button_has_a_trimmed_accessible_name(controller):
    """The card's terminal button carried only an icon and a tooltip.

    Qt does not use the tooltip as the accessible name, so screen readers
    announced it as just "button". Labelled buttons exposed their icon-gap
    padding ("  Switch to Work") as part of the name.
    """
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    window = ctl._ensure_window()
    window.update_states(ctl.profiles.states)
    ctl.popup.update_states(ctl.profiles.states)

    buttons = ctl.popup.findChildren(QPushButton) + window.findChildren(QPushButton)
    names = [_spoken_name(b) for b in buttons]

    assert buttons
    assert "" not in [name.strip() for name in names]
    assert [name for name in names if name != name.strip()] == []


def test_the_terminal_button_names_its_profile_and_explains_itself(controller):
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    ctl.popup.update_states(ctl.profiles.states)

    for card in ctl.popup._cards.values():
        name = card._state.profile.name
        assert card._launch_button.accessibleName() == f"Open a terminal for {name}"
        assert card._launch_button.accessibleDescription() == card._launch_button.toolTip()


def test_a_busy_card_is_named_by_what_it_is_doing(controller):
    ctl = controller("healthy")
    ctl.profiles.refresh_sync()
    ctl.popup.update_states(ctl.profiles.states)
    card = next(iter(ctl.popup._cards.values()))

    card.set_busy(True)
    assert card._switch_button.accessibleName() == "Working…"

    card.set_busy(False)
    assert card._switch_button.accessibleName() == card._primary_text(card._state)
