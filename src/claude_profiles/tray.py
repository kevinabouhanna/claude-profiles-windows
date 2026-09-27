"""Tray icon, context menu, and the controller that wires the app together."""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from .main_window import (
    TAB_ACCOUNTS,
    TAB_DASHBOARD,
    TAB_PRIVACY,
    TAB_SETTINGS,
    MainWindow,
)
from .models import DEFAULT_PROFILES, AccountList, ActivityEntry, ProfileState
from .resources import fluent_icons
from .resources.icons import tray_icon
from .services import autostart
from .services.cswap_client import CswapBackend, CswapError
from .services.hotkeys import HotkeyManager
from .services.notification_service import NotificationService
from .services.polling_service import PollingService, effective_interval
from .services.process_launcher import ProcessLauncher, find_claude
from .services.profile_service import ProfileService
from .services.settings_service import Settings, SettingsService
from .widgets import theme
from .widgets.compact_popup import CompactPopup
from .widgets.setup_dialog import SetupDialog
from .widgets.status_badge import format_age

SWITCH_HINT = (
    "Your next Claude Code message should use {name}. If the VS Code Claude "
    "panel still shows the former account, close and reopen that Claude panel."
)

RELOGIN_INSTRUCTIONS = (
    "<p><b>{name} needs you to sign in again.</b></p>"
    "<p>Claude Profiles never automates authentication, so these steps are "
    "manual and happen in your terminal:</p>"
    "<ol>"
    "<li>Open a terminal and run <code>claude</code>, then sign in as "
    "<b>{email}</b>.</li>"
    "<li>Once you are signed in, register the refreshed login with "
    "claude-swap:<br><code>cswap add --alias {alias}</code><br>"
    "If the slot already exists, <code>cswap add</code> updates it.</li>"
    "<li>Come back here and press <b>Refresh now</b>.</li>"
    "</ol>"
    "<p>This app cannot read or repair credentials itself - all credential "
    "handling belongs to claude-swap.</p>"
)


class TrayController(QObject):
    """Owns the tray icon and mediates between services and windows."""

    # Worker threads report back through signals. QTimer.singleShot cannot be
    # used from a non-GUI thread: it creates the timer in the calling thread,
    # which has no event loop, so the callback is silently never invoked.
    loginReady = Signal(object)  # ActiveStatus | None
    registerDone = Signal(bool, str, object)  # ok, message, ActiveStatus | None

    def __init__(
        self,
        app: QApplication,
        backend: CswapBackend,
        settings_service: SettingsService,
        *,
        mock_mode: bool = False,
        skip_windows_integration: bool = False,
    ) -> None:
        super().__init__()
        self._skip_windows_integration = skip_windows_integration
        self._app = app
        self._backend = backend
        self._settings_service = settings_service
        self._settings: Settings = settings_service.load()
        self._mock_mode = mock_mode
        self._window: MainWindow | None = None
        self._setup_dialog: SetupDialog | None = None
        self._last_error: str | None = None

        self.profiles = ProfileService(backend, settings_service, DEFAULT_PROFILES, parent=self)
        self.launcher = ProcessLauncher()

        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setToolTip("Claude Profiles")
        self.notifications = NotificationService(self.tray, self._settings.notifications_enabled)
        self.notifications.configure(
            enabled=self._settings.notifications_enabled,
            warn_pct=self._settings.warn_threshold_pct,
            critical_pct=self._settings.critical_threshold_pct,
        )

        self.polling = PollingService(backend, self._current_interval, parent=self)
        self.hotkeys = HotkeyManager(self._on_hotkey, parent=self)

        self.popup = CompactPopup(self.profiles.states)
        self._build_menu()
        self._connect()

        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

        if self._settings.hotkeys_enabled:
            self.hotkeys.enable(self._app)

    # -- setup --------------------------------------------------------------

    def _build_menu(self) -> None:
        menu = QMenu()
        self._header_action = QAction("Claude Profiles", menu)
        self._header_action.setEnabled(False)
        menu.addAction(self._header_action)
        menu.addSeparator()

        t = theme.tokens()
        self._switch_actions: dict[str, QAction] = {}
        for index, profile in enumerate(self.profiles.profiles, start=1):
            action = QAction(f"Switch to {profile.name}", menu)
            action.setIcon(
                fluent_icons.icon(
                    "work" if profile.key == "work" else "personal",
                    t.on_surface(profile.color),
                    16,
                )
            )
            action.setShortcut(f"Ctrl+Alt+{index}")
            action.triggered.connect(lambda _=False, key=profile.key: self._switch(key))
            menu.addAction(action)
            self._switch_actions[profile.key] = action
        menu.addSeparator()

        self._launch_actions: dict[str, QAction] = {}
        for profile in self.profiles.profiles:
            action = QAction(f"Launch Claude Code as {profile.name}", menu)
            action.setIcon(fluent_icons.icon("terminal", t.text_secondary, 16))
            action.setToolTip(
                "Opens an isolated terminal session; the active profile is unchanged."
            )
            action.triggered.connect(lambda _=False, key=profile.key: self._launch(key))
            menu.addAction(action)
            self._launch_actions[profile.key] = action
        menu.addSeparator()

        def command(glyph: str, text: str, slot) -> QAction:
            action = QAction(text, menu)
            action.setIcon(fluent_icons.icon(glyph, t.text_secondary, 16))
            action.triggered.connect(slot)
            return action

        menu.addAction(command("refresh", "Refresh now", self._refresh))
        menu.addAction(command("open_window", "Open full dashboard", self._show_window))
        menu.addAction(command("people", "Set up accounts…", self._show_setup))
        menu.addAction(
            command("settings", "Settings", lambda: self._show_window(tab=TAB_SETTINGS))
        )
        menu.addAction(
            command("lock", "Privacy", lambda: self._show_window(tab=TAB_PRIVACY))
        )
        menu.addSeparator()
        menu.addAction(command("power", "Quit", self._quit))

        self._menu = menu
        self.tray.setContextMenu(menu)

    def _connect(self) -> None:
        self.profiles.statesChanged.connect(self._on_states_changed)
        self.profiles.busyChanged.connect(self._on_busy_changed)
        self.profiles.activityAdded.connect(self._on_activity)
        self.profiles.switchSucceeded.connect(self._on_switch_succeeded)
        self.profiles.switchFailed.connect(self._on_switch_failed)
        self.profiles.setupSucceeded.connect(self._on_setup_succeeded)
        self.profiles.setupFailed.connect(self._on_setup_failed)
        self.profiles.schemaWarning.connect(self._on_schema_warning)
        self.loginReady.connect(self._apply_login)
        self.registerDone.connect(self._on_register_done)

        self.polling.pollSucceeded.connect(self._on_poll_succeeded)
        self.polling.pollFailed.connect(self._on_poll_failed)
        self.polling.pollStarted.connect(lambda: self._set_status("Refreshing…"))

        self.popup.switchRequested.connect(self._switch)
        self.popup.launchRequested.connect(self._launch)
        self.popup.reloginRequested.connect(self._show_relogin)
        self.popup.setupRequested.connect(self._show_setup)
        self.popup.refreshRequested.connect(self._refresh)
        self.popup.dashboardRequested.connect(self._show_window)
        self.popup.settingsRequested.connect(lambda: self._show_window(tab=TAB_SETTINGS))
        self.popup.quitRequested.connect(self._quit)
        self.popup.visibilityChanged.connect(self._on_ui_visibility_changed)

    def start(self) -> None:
        if not self._backend.is_available:
            self.profiles.log(
                "claude-swap was not found. Install it with: uv tool install claude-swap",
                level="error",
            )
        # Windows integration spawns PowerShell (0.5-3s cold) and may render
        # an icon. Doing that inline froze the tray for seconds at sign-in,
        # exactly when the machine is busiest, so it runs off the GUI thread.
        # It touches no widgets: the main window does not exist yet.
        threading.Thread(
            target=self._sync_windows_integration,
            name="windows-integration",
            daemon=True,
        ).start()
        self.polling.start()

    def _current_interval(self) -> float:
        """Poll faster while a window is on screen than while hidden."""
        return effective_interval(
            self._settings.refresh_interval_seconds, self._ui_visible()
        )

    def _ui_visible(self) -> bool:
        """Is anything actually on screen?

        QWidget.isVisible() stays True for a *minimized* window, so relying on
        it alone pins the faster foreground cadence on forever once the
        dashboard is minimized - polling every 30s with nothing displayed,
        which is the drain the adaptive cadence exists to avoid.
        """
        if self.popup.isVisible():
            return True
        window = self._window
        return window is not None and window.isVisible() and not window.isMinimized()

    @Slot(bool)
    def _on_ui_visibility_changed(self, visible: bool) -> None:
        # Take the new cadence immediately rather than after the pending wait.
        self.polling.reschedule()

    def _sync_windows_integration(self) -> None:
        """Make Windows match the saved preferences.

        Two things need reconciling at startup rather than only on a toggle:
        a default-on "start at sign-in" has to create its shortcut the first
        time the app runs, and a shortcut the user deleted by hand must be
        reflected back into the setting instead of being reported as enabled.
        """
        data_dir = self._settings_service.data_dir
        target = autostart.target_fingerprint()
        recorded = self._settings.autostart_target

        if self._skip_windows_integration:
            return

        result = autostart.reconcile(
            self._settings.launch_at_signin, recorded, data_dir
        )
        if result is not None:
            ok, message = result
            self.profiles.log(message, level="info" if ok else "error")
            if ok:
                self._settings = self._settings_service.update(autostart_target=target)
            # A failure here is transient - antivirus blocking script COM, a
            # redirected Start Menu folder, a slow cold PowerShell. Writing the
            # preference back to False would turn one bad launch into a
            # permanent opt-out that never retries, so the setting is left
            # alone and the next start tries again.

        entry = autostart.ensure_start_menu_entry(recorded, data_dir)
        if entry is not None:
            ok, message = entry
            self.profiles.log(message, level="info" if ok else "error")

    # -- tray interaction ---------------------------------------------------

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.popup.isVisible():
                self.popup.hide()
            else:
                self.popup.update_states(self.profiles.states)
                self.popup.show_near(self.tray.geometry().center())
                # Clicking the tray icon means "show me the numbers now", so
                # treat it as a refresh unless a reading just arrived.
                self.polling.poll_if_stale()

    def _on_hotkey(self, key: str) -> None:
        self._switch(key)

    # -- actions ------------------------------------------------------------

    @Slot(str)
    def _switch(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None or self.profiles.is_busy:
            return
        self._set_status(f"Switching to {state.profile.name}…")
        self.profiles.switch(key)

    @Slot(str)
    def _launch(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None:
            return
        command = self.profiles.run_command_for(key)
        if command is None:
            message = (
                f"{state.profile.name} is not registered with claude-swap yet, "
                "so there is nothing to launch."
            )
            self.profiles.log(message, level="error")
            self._notify_plain("Cannot launch", message)
            return

        result = self.launcher.launch(command, f"Claude — {state.profile.name}")
        if result.ok:
            if state.is_active:
                note = (
                    f"Opened a terminal for {state.profile.name}. It is already "
                    "the active login, so this session uses it directly."
                )
            else:
                note = (
                    f"Opened an isolated terminal session for {state.profile.name}. "
                    "The globally active profile is unchanged."
                )
            self.profiles.log(note)
            self.popup.show_hint(note)
        else:
            self.profiles.log(f"Launch failed - {result.message}", level="error")
            self._notify_plain("Launch failed", result.message)

    @Slot()
    def _refresh(self) -> None:
        if not self.polling.poll_now():
            self._set_status("A refresh is already running…")

    @Slot(str)
    def _show_relogin(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None:
            return
        email = state.account.email if state.account else "that account"
        box = QMessageBox()
        box.setWindowTitle("Re-authentication required")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            RELOGIN_INSTRUCTIONS.format(
                name=state.profile.name, email=email, alias=state.profile.alias
            )
        )
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _show_window(self, tab: int = TAB_DASHBOARD) -> None:
        window = self._ensure_window()
        window.show_tab(tab)
        window.set_accounts(self.profiles.last_accounts)
        window.update_states(self.profiles.states)
        window.set_activity(self.profiles.recent_activity())
        self._present(window)
        self.polling.poll_if_stale()

    def _present(self, window) -> None:
        """Bring a window to the foreground from inside a popup or tray menu.

        Both the flyout and the tray menu are Qt popups holding an input grab.
        Windows refuses a foreground change while that grab is held, so calling
        show()/activateWindow() directly leaves the window open but *behind*
        whatever the user was looking at - indistinguishable from nothing
        happening. Releasing the grab first and activating on the next event
        loop turn is what makes it actually appear.
        """
        self.popup.hide()

        def bring_forward() -> None:
            window.setWindowState(
                (window.windowState() & ~Qt.WindowState.WindowMinimized)
                | Qt.WindowState.WindowActive
            )
            window.show()
            window.raise_()
            window.activateWindow()

        QTimer.singleShot(0, bring_forward)

    @Slot()
    def _show_setup(self, key: str = "") -> None:
        """Open setup.

        With a profile key this opens the focused wizard for that profile,
        which is what the card buttons want. Without one it opens the Accounts
        page, which gives an overview of every account claude-swap knows.
        """
        if key:
            self._open_setup_dialog(key)
            return
        self._show_window(tab=TAB_ACCOUNTS)
        self._refresh_setup()

    def _open_setup_dialog(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None:
            return
        self.popup.hide()

        dialog = SetupDialog(state.profile, self.profiles.read_current_login)
        dialog.signInRequested.connect(lambda: self._start_sign_in(dialog))
        dialog.registerRequested.connect(
            lambda profile_key: self._register_from_dialog(dialog, profile_key)
        )
        dialog.finished.connect(lambda _: self._on_setup_dialog_closed())
        self._setup_dialog = dialog

        # Same foreground rule as the main window: release the popup grab, then
        # show on the next turn or the dialog opens behind everything.
        def present() -> None:
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            dialog.start()

        QTimer.singleShot(0, present)

    def _on_setup_dialog_closed(self) -> None:
        self._setup_dialog = None
        self._refresh()

    def _start_sign_in(self, dialog: SetupDialog) -> None:
        """Launch ``claude auth login``, which opens the browser for OAuth."""
        claude = find_claude()
        if claude is None:
            dialog.set_result(
                "Could not find the Claude Code command. Open a terminal and "
                "run: claude auth login",
                ok=False,
            )
            return

        # keep_open=False: the window closes itself once sign-in succeeds,
        # and only sticks around if the command failed.
        result = self.launcher.launch(
            [claude, "auth", "login"],
            f"Sign in — {dialog.profile.name}",
            keep_open=False,
        )
        if result.ok:
            self.profiles.log("Opened a terminal to sign in to Claude Code")
            dialog.begin_waiting()
        else:
            self.profiles.log(f"Sign-in terminal failed - {result.message}", level="error")
            dialog.set_result(result.message, ok=False)

    def _register_from_dialog(self, dialog: SetupDialog, key: str) -> None:
        if self.profiles.is_busy:
            return
        dialog.set_busy(True)
        self.profiles.run_in_background(self._do_register_for_dialog, key)

    def _do_register_for_dialog(self, key: str) -> None:
        ok, message = self.profiles.register_current_as(key)
        self.registerDone.emit(ok, message, self.profiles.read_current_login())

    @Slot(bool, str, object)
    def _on_register_done(self, ok: bool, message: str, status) -> None:
        dialog = self._setup_dialog
        if dialog is not None:
            dialog.set_busy(False)
            dialog.set_result(message, ok=ok)
            dialog.update_status(status)
        self._after_setup(status)

    def _refresh_setup(self) -> None:
        if self._window is None:
            return
        self._window.setup_page.update_accounts(
            self.profiles.last_accounts, self.profiles.states
        )
        self.profiles.run_in_background(self._read_login)

    def _read_login(self) -> None:
        self.loginReady.emit(self.profiles.read_current_login())

    def _apply_login(self, status) -> None:
        if self._window is not None:
            self._window.setup_page.update_login(status)

    @Slot()
    def _open_sign_in(self) -> None:
        """Open a terminal running Claude Code so the user can sign in."""
        claude = find_claude()
        if claude is None:
            message = (
                "Could not find the Claude Code command. Open a terminal "
                "yourself and run: claude auth login"
            )
            self.profiles.log(message, level="error")
            if self._window is not None:
                self._window.setup_page.set_result(message, ok=False)
            return

        result = self.launcher.launch(
            [claude, "auth", "login"], "Sign in to Claude Code", keep_open=False
        )
        if result.ok:
            message = (
                "Opened a terminal running claude auth login. Your browser "
                "should open for sign-in; come back and press Re-check when "
                "it finishes."
            )
            self.profiles.log("Opened a terminal to sign in to Claude Code")
        else:
            message = result.message
            self.profiles.log(f"Sign-in terminal failed - {message}", level="error")
        if self._window is not None:
            self._window.setup_page.set_result(message, ok=result.ok)

    @Slot(str)
    def _register_profile(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None or self.profiles.is_busy:
            return

        login = self.profiles.last_status
        email = login.email if login and login.email else "the signed-in account"
        confirm = QMessageBox()
        confirm.setWindowTitle(f"Register as {state.profile.name}")
        confirm.setTextFormat(Qt.TextFormat.RichText)
        confirm.setText(
            f"<p>Store <b>{email}</b> as your <b>{state.profile.name}</b> profile?</p>"
            "<p>claude-swap records whichever account Claude Code is signed in "
            "as right now. If that address is not the one you want, close this, "
            "sign in as the right account, then press Re-check.</p>"
        )
        confirm.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Ok:
            return

        self.profiles.run_in_background(self._do_register, key)

    def _do_register(self, key: str) -> None:
        ok, message = self.profiles.register_current_as(key)
        self.registerDone.emit(ok, message, self.profiles.read_current_login())

    @Slot(int, str)
    def _assign_alias(self, number: int, key: str) -> None:
        if self.profiles.is_busy:
            return
        self.profiles.run_in_background(self._do_assign_alias, number, key)

    def _do_assign_alias(self, number: int, key: str) -> None:
        ok, message = self.profiles.assign_alias(number, key)
        self.registerDone.emit(ok, message, self.profiles.read_current_login())

    def _after_setup(self, status) -> None:
        if self._window is None:
            return
        self._window.setup_page.update_login(status)
        self._window.setup_page.update_accounts(
            self.profiles.last_accounts, self.profiles.states
        )

    def _ensure_window(self) -> MainWindow:
        if self._window is None:
            window = MainWindow(
                self.profiles.states,
                self._settings,
                data_dir=str(self._settings_service.data_dir),
                mock_mode=self._mock_mode,
            )
            window.switchRequested.connect(self._switch)
            window.launchRequested.connect(self._launch)
            window.reloginRequested.connect(self._show_relogin)
            window.setupRequested.connect(self._show_setup)
            window.refreshRequested.connect(self._refresh)
            window.settingsChanged.connect(self._on_settings_changed)
            window.clearHistoryRequested.connect(self._clear_history)
            window.openDataFolderRequested.connect(self._open_data_folder)

            window.setup_page.signInRequested.connect(self._open_sign_in)
            window.setup_page.registerRequested.connect(self._register_profile)
            window.setup_page.assignAliasRequested.connect(self._assign_alias)
            window.setup_page.refreshRequested.connect(self._refresh_setup)
            window.visibilityChanged.connect(self._on_ui_visibility_changed)
            window.accountsTabShown.connect(self._refresh_setup)
            self._window = window
        return self._window

    @Slot()
    def _clear_history(self) -> None:
        self.profiles.clear_activity()
        if self._window is not None:
            self._window.set_activity(self.profiles.recent_activity())

    @Slot()
    def _open_data_folder(self) -> None:
        path = self._settings_service.ensure_data_dir()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    @Slot(object)
    def _on_settings_changed(self, settings: Settings) -> None:
        previous = self._settings
        self._settings = self._settings_service.save(settings)

        self.notifications.configure(
            enabled=self._settings.notifications_enabled,
            warn_pct=self._settings.warn_threshold_pct,
            critical_pct=self._settings.critical_threshold_pct,
        )
        if self._settings.refresh_interval_seconds != previous.refresh_interval_seconds:
            self.polling.reschedule()
            self.profiles.log(
                f"Refresh interval set to {self._settings.refresh_interval_seconds}s"
            )

        if self._settings.launch_at_signin != previous.launch_at_signin:
            ok, message = autostart.set_enabled(self._settings.launch_at_signin)
            self.profiles.log(message, level="info" if ok else "error")
            if not ok and self._window:
                # Reflect reality in the checkbox without persisting it, so the
                # user sees the failure but the preference survives a retry.
                self._window.load_settings(self._settings)

        if self._settings.hotkeys_enabled != previous.hotkeys_enabled:
            if self._settings.hotkeys_enabled:
                ok, message = self.hotkeys.enable(self._app)
                if not ok:
                    self._settings = self._settings_service.update(hotkeys_enabled=False)
                    if self._window:
                        self._window.load_settings(self._settings)
            else:
                self.hotkeys.disable(self._app)
                ok, message = True, "Global shortcuts disabled."
            self.profiles.log(message, level="info" if ok else "error")
            if self._window:
                self._window.set_hotkey_status(message)

    def _quit(self) -> None:
        self.polling.stop()
        self.hotkeys.disable(self._app)
        self.tray.hide()
        self._app.quit()

    # -- reacting to services ----------------------------------------------

    @Slot(object)
    def _on_poll_succeeded(self, accounts: AccountList) -> None:
        self._last_error = None
        self.profiles.apply_accounts(accounts)
        self.notifications.check_thresholds(self.profiles.states)
        self._set_status(self._freshness_line())

    @Slot(object)
    def _on_poll_failed(self, error: CswapError) -> None:
        self._last_error = error.user_message
        self.profiles.apply_error(error)
        delay = self.polling.coordinator.next_delay()
        self._set_status(
            f"Refresh failed — {error.user_message} Retrying in {delay / 60:.0f} min. "
            "Showing last known values."
        )

    @Slot()
    def _on_states_changed(self) -> None:
        states = self.profiles.states
        self.popup.update_states(states)
        if self._window is not None:
            self._window.set_accounts(self.profiles.last_accounts)
            self._window.update_states(states)
        self._update_tray_appearance(states)
        for key, action in self._launch_actions.items():
            state = self.profiles.state(key)
            action.setEnabled(bool(state and state.account and state.account.number is not None))
        for key, action in self._switch_actions.items():
            state = self.profiles.state(key)
            action.setEnabled(bool(state and state.account) and not (state and state.is_active))

    @Slot(bool)
    def _on_busy_changed(self, busy: bool) -> None:
        self.popup.set_busy(busy)
        if self._window is not None:
            self._window.set_busy(busy)
        for action in self._switch_actions.values():
            action.setEnabled(not busy and action.isEnabled())
        if not busy:
            self._on_states_changed()

    @Slot(object)
    def _on_activity(self, entry: ActivityEntry) -> None:
        if self._window is not None:
            self._window.append_activity(entry)

    @Slot(str)
    def _on_switch_succeeded(self, key: str) -> None:
        state = self.profiles.state(key)
        if state is None:
            return
        email = state.account.email if state.account else ""
        self.notifications.notify_switch(state.profile.name, email)
        hint = SWITCH_HINT.format(name=state.profile.name)
        self.popup.show_hint(hint)
        if self._window is not None:
            self._window.show_banner(hint)
        self._set_status(self._freshness_line())

    @Slot(str, str)
    def _on_switch_failed(self, key: str, message: str) -> None:
        state = self.profiles.state(key)
        name = state.profile.name if state else key
        self._notify_plain(f"Could not switch to {name}", message)
        self._set_status(message)

    @Slot(str, str)
    def _on_setup_succeeded(self, key: str, message: str) -> None:
        if self._window is not None:
            self._window.setup_page.set_result(message, ok=True)
        self._notify_plain("Claude Profiles", message)
        self._set_status(message)

    @Slot(str, str)
    def _on_setup_failed(self, key: str, message: str) -> None:
        if self._window is not None:
            self._window.setup_page.set_result(message, ok=False)
        self._set_status(message)

    @Slot(int)
    def _on_schema_warning(self, version: int) -> None:
        message = (
            f"claude-swap reported an unfamiliar data format (schemaVersion "
            f"{version}). Readings may be incomplete; updating this app may help."
        )
        self.profiles.log(message, level="warning")
        if self._window is not None:
            self._window.show_banner(message)

    # -- presentation -------------------------------------------------------

    def _notify_plain(self, title: str, message: str) -> None:
        self.notifications.notify(title, message, force=True)

    def _set_status(self, text: str) -> None:
        self.popup.set_status(text)
        if self._window is not None:
            self._window.set_status(text)

    def _freshness_line(self) -> str:
        active = self.profiles.active_state
        if active is None:
            return "No active profile is registered with claude-swap."
        age = active.account.effective_age_seconds if active.account else None
        stale = " · showing last known values" if active.account and active.account.is_stale else ""
        return f"{active.profile.name} active · updated {format_age(age)}{stale}"

    def _update_tray_appearance(self, states: tuple[ProfileState, ...]) -> None:
        active = next((s for s in states if s.is_active), None)
        if active is None:
            self.tray.setIcon(tray_icon())
            self.tray.setToolTip("Claude Profiles — no active profile")
            self._header_action.setText("Claude Profiles")
            return

        self.tray.setIcon(tray_icon(active.profile.color, active.profile.name))
        parts = [f"{active.profile.name} active"]
        usage = active.account.effective_usage if active.account else None
        if usage and usage.five_hour:
            parts.append(f"5h {usage.five_hour.pct:.0f}%")
        if usage and usage.seven_day:
            parts.append(f"7d {usage.seven_day.pct:.0f}%")
        if active.account and active.account.is_stale:
            parts.append("stale")
        tooltip = "Claude Profiles — " + " · ".join(parts)
        self.tray.setToolTip(tooltip)
        self._header_action.setText(f"Claude Profiles — {active.profile.name} active")
