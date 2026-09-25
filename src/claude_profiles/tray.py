"""Tray icon, context menu, and the controller that wires the app together."""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Slot
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
from .resources.icons import tray_icon
from .services import autostart
from .services.cswap_client import CswapBackend, CswapError
from .services.hotkeys import HotkeyManager
from .services.notification_service import NotificationService
from .services.polling_service import PollingService
from .services.process_launcher import ProcessLauncher, find_claude
from .services.profile_service import ProfileService
from .services.settings_service import Settings, SettingsService
from .widgets.compact_popup import CompactPopup
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

    def __init__(
        self,
        app: QApplication,
        backend: CswapBackend,
        settings_service: SettingsService,
        *,
        mock_mode: bool = False,
    ) -> None:
        super().__init__()
        self._app = app
        self._backend = backend
        self._settings_service = settings_service
        self._settings: Settings = settings_service.load()
        self._mock_mode = mock_mode
        self._window: MainWindow | None = None
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

        self.polling = PollingService(
            backend, lambda: float(self._settings.refresh_interval_seconds), parent=self
        )
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

        self._switch_actions: dict[str, QAction] = {}
        for index, profile in enumerate(self.profiles.profiles, start=1):
            action = QAction(f"Switch to {profile.name}", menu)
            action.setShortcut(f"Ctrl+Alt+{index}")
            action.triggered.connect(lambda _=False, key=profile.key: self._switch(key))
            menu.addAction(action)
            self._switch_actions[profile.key] = action
        menu.addSeparator()

        self._launch_actions: dict[str, QAction] = {}
        for profile in self.profiles.profiles:
            action = QAction(f"Launch Claude Code as {profile.name}", menu)
            action.setToolTip(
                "Opens an isolated terminal session; the active profile is unchanged."
            )
            action.triggered.connect(lambda _=False, key=profile.key: self._launch(key))
            menu.addAction(action)
            self._launch_actions[profile.key] = action
        menu.addSeparator()

        menu.addAction(QAction("Refresh now", menu, triggered=self._refresh))
        menu.addAction(QAction("Open full dashboard", menu, triggered=self._show_window))
        menu.addAction(
            QAction("Set up accounts…", menu, triggered=self._show_setup)
        )
        menu.addAction(
            QAction("Settings", menu, triggered=lambda: self._show_window(tab=TAB_SETTINGS))
        )
        menu.addAction(
            QAction("Privacy", menu, triggered=lambda: self._show_window(tab=TAB_PRIVACY))
        )
        menu.addSeparator()
        menu.addAction(QAction("Quit", menu, triggered=self._quit))

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

        self.polling.pollSucceeded.connect(self._on_poll_succeeded)
        self.polling.pollFailed.connect(self._on_poll_failed)
        self.polling.pollStarted.connect(lambda: self._set_status("Refreshing…"))

        self.popup.switchRequested.connect(self._switch)
        self.popup.launchRequested.connect(self._launch)
        self.popup.reloginRequested.connect(self._show_relogin)
        self.popup.setupRequested.connect(lambda _key: self._show_setup())
        self.popup.refreshRequested.connect(self._refresh)
        self.popup.dashboardRequested.connect(self._show_window)
        self.popup.settingsRequested.connect(lambda: self._show_window(tab=TAB_SETTINGS))
        self.popup.quitRequested.connect(self._quit)

    def start(self) -> None:
        if not self._backend.is_available:
            self.profiles.log(
                "claude-swap was not found. Install it with: uv tool install claude-swap",
                level="error",
            )
        self.polling.start()

    # -- tray interaction ---------------------------------------------------

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.popup.isVisible():
                self.popup.hide()
            else:
                self.popup.update_states(self.profiles.states)
                self.popup.show_near(self.tray.geometry().center())

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
        window.update_states(self.profiles.states)
        window.set_activity(self.profiles.recent_activity())
        window.show()
        window.raise_()
        window.activateWindow()
        self.popup.hide()

    @Slot()
    def _show_setup(self) -> None:
        """Open the Accounts page and read the current login fresh."""
        self._show_window(tab=TAB_ACCOUNTS)
        self._refresh_setup()

    def _refresh_setup(self) -> None:
        if self._window is None:
            return
        self._window.setup_page.update_accounts(
            self.profiles.last_accounts, self.profiles.states
        )
        self.profiles.run_in_background(self._read_login)

    def _read_login(self) -> None:
        status = self.profiles.read_current_login()
        QTimer.singleShot(0, lambda: self._apply_login(status))

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
                "yourself and run: claude"
            )
            self.profiles.log(message, level="error")
            if self._window is not None:
                self._window.setup_page.set_result(message, ok=False)
            return

        result = self.launcher.launch([claude], "Sign in to Claude Code")
        if result.ok:
            message = (
                "Opened a terminal running Claude Code. Sign in there (type "
                "/login to change account), then press Re-check."
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
        self.profiles.register_current_as(key)
        status = self.profiles.read_current_login()
        QTimer.singleShot(0, lambda: self._after_setup(status))

    @Slot(int, str)
    def _assign_alias(self, number: int, key: str) -> None:
        if self.profiles.is_busy:
            return
        self.profiles.run_in_background(self._do_assign_alias, number, key)

    def _do_assign_alias(self, number: int, key: str) -> None:
        self.profiles.assign_alias(number, key)
        status = self.profiles.read_current_login()
        QTimer.singleShot(0, lambda: self._after_setup(status))

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
            window.setupRequested.connect(lambda _key: self._show_setup())
            window.refreshRequested.connect(self._refresh)
            window.settingsChanged.connect(self._on_settings_changed)
            window.clearHistoryRequested.connect(self._clear_history)
            window.openDataFolderRequested.connect(self._open_data_folder)

            window.setup_page.signInRequested.connect(self._open_sign_in)
            window.setup_page.registerRequested.connect(self._register_profile)
            window.setup_page.assignAliasRequested.connect(self._assign_alias)
            window.setup_page.refreshRequested.connect(self._refresh_setup)
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
            if not ok:
                self._settings = self._settings_service.update(
                    launch_at_signin=autostart.is_enabled()
                )
                if self._window:
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

        self.tray.setIcon(tray_icon(active.profile.color, active.profile.name[:1]))
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
