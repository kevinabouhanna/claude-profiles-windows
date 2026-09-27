"""The main window: left navigation, one page per destination.

Organised by what the user came to do rather than by implementation detail:

    Overview   both profiles, their usage, and a refresh      (top)
    Accounts   which Claude account each profile uses          (top)
    Activity   the safe, local history of what happened        (top)
    Privacy    what is and is not stored, and your data        (footer)
    Settings   General, Notifications, Keyboard shortcuts,     (footer)
               About

The old tabbed layout mixed these: the activity log lived on the dashboard,
"Clear history" appeared in two unrelated places, and Settings grouped global
shortcuts under "Windows integration" beside start-up behaviour.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from . import __version__
from .models import ActivityEntry, ProfileState
from .resources import fluent_icons
from .resources.icons import render_app_tile
from .services.settings_service import Settings
from .widgets import theme
from .widgets.buttons import text_button
from .widgets.controls import Stepper, ToggleSwitch, format_interval
from .widgets.navigation import NavigationPane
from .widgets.profile_card import ProfileCard
from .widgets.settings_card import BulletCard, InfoBar, Page, SettingsCard
from .widgets.setup_page import SetupPage

# Page order. Named so callers never depend on a position.
TAB_DASHBOARD = 0
TAB_ACCOUNTS = 1
TAB_ACTIVITY = 2
TAB_PRIVACY = 3
TAB_SETTINGS = 4

REFRESH_CHOICES = (30, 60, 120, 300, 600, 900, 1800, 3600)
ACTIVITY_LIMIT = 400

PRIVACY_STORED = [
    "Your preferences: how often to check usage, alert thresholds, and whether "
    "start-up and global shortcuts are on.",
    "The profile aliases (personal, work) used to address claude-swap. They are "
    "not secrets.",
    "A short local history of status lines, with addresses masked and anything "
    "that looks like a token removed before it is saved.",
]

PRIVACY_NOT_STORED = [
    "Access tokens, refresh tokens, API keys, session cookies or passwords. The "
    "app never reads or writes them.",
    "Anything from .claude\\.credentials.json, .claude-swap-backup\\, or Claude "
    "Code's session files.",
    "Raw output from claude-swap. Only parsed, redacted fields are kept.",
    "Telemetry, analytics, crash reports, cloud sync or remote settings. The app "
    "makes no network connections of its own.",
]

PRIVACY_LIMITS = [
    "Switching changes the account Claude Code uses for new sessions. It does "
    "not sign your browser in or out of claude.ai.",
    "Nothing is ever closed for you: Claude Code, VS Code, terminals and open "
    "chats are left alone.",
    "All account data comes from claude-swap. The app calls no Anthropic API "
    "itself.",
]


def _percent(value: int) -> str:
    return f"{value}%"


class MainWindow(QMainWindow):
    """Navigation on the left, the selected page on the right."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    setupRequested = Signal(str)
    refreshRequested = Signal()
    settingsChanged = Signal(object)  # Settings
    clearHistoryRequested = Signal()
    openDataFolderRequested = Signal()
    visibilityChanged = Signal(bool)
    # The Accounts page needs data fetched for it; selecting it must say so,
    # or it sits on "Checking..." with nothing filled in.
    accountsTabShown = Signal()

    def __init__(
        self,
        states: tuple[ProfileState, ...],
        settings: Settings,
        *,
        data_dir: str,
        mock_mode: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle("Claude Profiles")
        self.resize(1000, 700)
        self.setMinimumSize(780, 540)
        self._loading_settings = False
        self._settings = settings
        self._accounts = None
        self._cards: dict[str, ProfileCard] = {}
        self._profile_names = [s.profile.name for s in states]

        self.setup_page = SetupPage(tuple(s.profile for s in states))

        self._nav = NavigationPane()
        self._stack = QStackedWidget()
        pages = (
            ("home", "Overview", self._build_overview(states, mock_mode), False),
            ("accounts", "Accounts", self._build_accounts(), False),
            ("history", "Activity", self._build_activity(), False),
            ("shield", "Privacy", self._build_privacy(data_dir), True),
            ("settings", "Settings", self._build_settings(settings), True),
        )
        for glyph, label, page, footer in pages:
            self._nav.add_item(glyph, label, footer=footer)
            self._stack.addWidget(page)
        self._nav.currentChanged.connect(self._on_page_changed)

        central = QWidget()
        central.setObjectName("windowRoot")
        central.setStyleSheet(
            f"#windowRoot {{ background-color: {theme.tokens().background}; }}"
        )
        row = QHBoxLayout(central)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._nav)
        row.addWidget(self._stack, 1)
        self.setCentralWidget(central)

        self._nav.set_current(TAB_DASHBOARD)

    # -- overview -----------------------------------------------------------

    def _build_overview(self, states: tuple[ProfileState, ...], mock_mode: bool) -> Page:
        page = Page("Overview")

        self._refresh_button = text_button("refresh", "Refresh")
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        page.add_header_action(self._refresh_button)

        if mock_mode:
            demo = InfoBar("info", closable=False)
            demo.show_message(
                "Demo mode. Every figure here is synthetic; no real account is "
                "being read or changed."
            )
            page.add(demo)

        self._banner = InfoBar("info")
        page.add(self._banner)

        cards = QWidget()
        row = QHBoxLayout(cards)
        row.setContentsMargins(0, 4, 0, 0)
        row.setSpacing(12)
        for state in states:
            card = ProfileCard(state, compact=False)
            card.switchRequested.connect(self.switchRequested.emit)
            card.launchRequested.connect(self.launchRequested.emit)
            card.reloginRequested.connect(self.reloginRequested.emit)
            card.setupRequested.connect(self.setupRequested.emit)
            row.addWidget(card)
            self._cards[state.profile.key] = card
        page.add(cards)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(
            theme.text_css(theme.CAPTION, "tertiary") + " padding-top: 8px;"
        )
        page.add(self._status_label)
        page.finish()
        return page

    # -- accounts -----------------------------------------------------------

    def _build_accounts(self) -> Page:
        page = Page("Accounts")
        # The page supplies its own margins when shown inside the window.
        self.setup_page.layout().setContentsMargins(0, 0, 0, 0)
        page.add(self.setup_page)
        page.finish()
        return page

    # -- activity -----------------------------------------------------------

    def _build_activity(self) -> Page:
        page = Page(
            "Activity",
            "What happened, kept on this computer. Addresses are masked and "
            "anything that looks like a token is removed before it is saved.",
            scroll=False,
        )
        clear = text_button("delete", "Clear history")
        clear.clicked.connect(self.clearHistoryRequested.emit)
        page.add_header_action(clear)

        t = theme.tokens()
        self._activity_list = QListWidget()
        self._activity_list.setObjectName("activityList")
        self._activity_list.setIconSize(fluent_icons.icon_size(14))
        self._activity_list.setStyleSheet(
            f"#activityList {{ background-color: {t.card}; border: 1px solid {t.card_stroke};"
            f" border-radius: {theme.RADIUS_CARD}px; padding: 6px; }}"
            "#activityList::item { padding: 3px 8px; }"
        )
        page.add(self._activity_list, 1)

        self._activity_empty = QLabel("Nothing has been recorded yet.")
        self._activity_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._activity_empty.setStyleSheet(theme.text_css(theme.BODY, "tertiary"))
        page.add(self._activity_empty)
        self._update_activity_empty()
        return page

    # -- privacy ------------------------------------------------------------

    def _build_privacy(self, data_dir: str) -> Page:
        page = Page(
            "Privacy",
            "Claude Profiles runs entirely on this computer. Its only outside "
            "interaction is running the local cswap command and reading what it "
            "prints.",
        )
        page.add_section("What this app keeps")
        page.add(BulletCard("check", "success", PRIVACY_STORED))
        page.add_section("What it never keeps")
        page.add(BulletCard("cancel", "secondary", PRIVACY_NOT_STORED))
        page.add_section("What it can't do")
        page.add(BulletCard("info", "secondary", PRIVACY_LIMITS))

        page.add_section("Your data")
        open_button = QPushButton("Open")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.clicked.connect(self.openDataFolderRequested.emit)
        page.add(SettingsCard("folder", "Open data folder", data_dir, open_button))

        clear_button = QPushButton("Clear")
        clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_button.clicked.connect(self.clearHistoryRequested.emit)
        page.add(
            SettingsCard(
                "delete",
                "Clear local activity history",
                "Removes the status lines kept on this computer. Your settings "
                "and accounts are not affected.",
                clear_button,
            )
        )
        page.finish()
        return page

    # -- settings -----------------------------------------------------------

    def _build_settings(self, settings: Settings) -> Page:
        page = Page("Settings")

        page.add_section("General")
        self._autostart_toggle = ToggleSwitch()
        self._autostart_toggle.setChecked(settings.launch_at_signin)
        self._autostart_toggle.toggled.connect(self._emit_settings)
        page.add(
            SettingsCard(
                "power",
                "Start with Windows",
                "Opens Claude Profiles in the notification area when you sign "
                "in. Uses a Startup-folder shortcut, not the registry.",
                self._autostart_toggle,
            )
        )

        self._interval_stepper = Stepper(
            settings.refresh_interval_seconds,
            choices=REFRESH_CHOICES,
            formatter=format_interval,
            label="usage check interval",
        )
        self._interval_stepper.valueChanged.connect(self._emit_settings)
        page.add(
            SettingsCard(
                "clock",
                "Check usage every",
                "While the app is in the notification area. With a window open "
                "it checks every 30 seconds.",
                self._interval_stepper,
            )
        )

        page.add_section("Notifications")
        self._alerts_toggle = ToggleSwitch()
        self._alerts_toggle.setChecked(settings.notifications_enabled)
        self._alerts_toggle.toggled.connect(self._on_alerts_toggled)
        page.add(
            SettingsCard(
                "bell",
                "Usage alerts",
                "Tells you once when a profile crosses a threshold, not on every "
                "check. Confirmations of a switch you made always appear.",
                self._alerts_toggle,
            )
        )

        self._warn_stepper = Stepper(
            settings.warn_threshold_pct,
            minimum=5,
            maximum=settings.critical_threshold_pct - 1,
            step=5,
            formatter=_percent,
            label="warning threshold",
        )
        self._warn_stepper.valueChanged.connect(self._on_threshold_changed)
        self._warn_card = page.add(
            SettingsCard(
                "warning",
                "Warn at",
                "The first alert, while there is still some room left.",
                self._warn_stepper,
            )
        )

        self._critical_stepper = Stepper(
            settings.critical_threshold_pct,
            minimum=settings.warn_threshold_pct + 1,
            maximum=100,
            step=5,
            formatter=_percent,
            label="critical threshold",
        )
        self._critical_stepper.valueChanged.connect(self._on_threshold_changed)
        self._critical_card = page.add(
            SettingsCard(
                "error",
                "Critical at",
                "The second alert, when the quota is nearly gone.",
                self._critical_stepper,
            )
        )
        self._sync_threshold_availability()

        page.add_section("Keyboard shortcuts")
        self._hotkeys_toggle = ToggleSwitch()
        self._hotkeys_toggle.setChecked(settings.hotkeys_enabled)
        self._hotkeys_toggle.toggled.connect(self._emit_settings)
        first, second = (self._profile_names + ["the first profile", "the second"])[:2]
        self._hotkeys_card = page.add(
            SettingsCard(
                "keyboard",
                "Global shortcuts",
                f"Ctrl+Alt+1 switches to {first} and Ctrl+Alt+2 to {second}, "
                "from any app.",
                self._hotkeys_toggle,
            )
        )

        page.add_section("About")
        tile = QPixmap.fromImage(render_app_tile(20))
        page.add(
            SettingsCard(
                "",
                "Claude Profiles",
                f"Version {__version__}. Runs entirely on this computer; all "
                "account handling is done by claude-swap.",
                icon=tile,
            )
        )
        page.finish()
        return page

    def _on_alerts_toggled(self, _checked: bool) -> None:
        self._sync_threshold_availability()
        self._emit_settings()

    def _sync_threshold_availability(self) -> None:
        on = self._alerts_toggle.isChecked()
        self._warn_card.set_available(on)
        self._critical_card.set_available(on)

    def _on_threshold_changed(self, _value: int) -> None:
        # Keep the warning strictly below the critical level by narrowing each
        # control's range to the other's value, rather than correcting after.
        self._critical_stepper.set_range(self._warn_stepper.value() + 1, 100)
        self._warn_stepper.set_range(5, self._critical_stepper.value() - 1)
        self._emit_settings()

    def _emit_settings(self, *_args) -> None:
        if self._loading_settings:
            return
        # Update the settings held rather than constructing fresh ones: this
        # page owns six fields, and a new Settings() would reset the rest -
        # autostart_target and profile_aliases - to defaults on every change.
        self.settingsChanged.emit(
            replace(
                self._settings,
                refresh_interval_seconds=self._interval_stepper.value(),
                launch_at_signin=self._autostart_toggle.isChecked(),
                notifications_enabled=self._alerts_toggle.isChecked(),
                warn_threshold_pct=self._warn_stepper.value(),
                critical_threshold_pct=self._critical_stepper.value(),
                hotkeys_enabled=self._hotkeys_toggle.isChecked(),
            )
        )

    def load_settings(self, settings: Settings) -> None:
        """Push values back into the controls without re-emitting."""
        self._settings = settings
        self._loading_settings = True
        try:
            self._interval_stepper.setValue(settings.refresh_interval_seconds)
            self._autostart_toggle.setChecked(settings.launch_at_signin)
            self._alerts_toggle.setChecked(settings.notifications_enabled)
            # Widen first so neither value is clamped by the other's old range.
            self._warn_stepper.set_range(5, 99)
            self._critical_stepper.set_range(2, 100)
            self._warn_stepper.setValue(settings.warn_threshold_pct)
            self._critical_stepper.setValue(settings.critical_threshold_pct)
            self._critical_stepper.set_range(settings.warn_threshold_pct + 1, 100)
            self._warn_stepper.set_range(5, settings.critical_threshold_pct - 1)
            self._hotkeys_toggle.setChecked(settings.hotkeys_enabled)
            self._sync_threshold_availability()
        finally:
            self._loading_settings = False

    def set_hotkey_status(self, message: str, ok: bool = True) -> None:
        tone = "tertiary"
        if not ok:
            tone = "critical"
        elif "except" in message:
            tone = "caution"
        self._hotkeys_card.set_status(message, tone)

    # -- navigation ---------------------------------------------------------

    def show_tab(self, index: int) -> None:
        unchanged = self._nav.current_index == index
        self._nav.set_current(index)
        # Reopening the Accounts page should still refresh it.
        if unchanged and index == TAB_ACCOUNTS:
            self.accountsTabShown.emit()

    def current_tab(self) -> int:
        return self._nav.current_index

    def _on_page_changed(self, index: int) -> None:
        self._stack.setCurrentIndex(index)
        if index == TAB_ACCOUNTS:
            self.accountsTabShown.emit()

    # -- updates ------------------------------------------------------------

    def update_states(self, states: tuple[ProfileState, ...]) -> None:
        for state in states:
            card = self._cards.get(state.profile.key)
            if card is not None:
                card.set_state(state)
        # Keep the Accounts page in step with every refresh.
        self.setup_page.update_accounts(self._accounts, states)

    def set_accounts(self, accounts) -> None:
        self._accounts = accounts

    def set_busy(self, busy: bool) -> None:
        for card in self._cards.values():
            card.set_busy(busy)
        self.setup_page.set_busy(busy)
        self._refresh_button.setEnabled(not busy)
        self._refresh_button.setText("  Refreshing…" if busy else "  Refresh")

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def show_banner(self, text: str, severity: str = "info") -> None:
        self._banner.show_message(text, severity)

    # -- activity -----------------------------------------------------------

    def set_activity(self, entries: list[ActivityEntry]) -> None:
        self._activity_list.clear()
        for entry in reversed(entries):
            self.append_activity(entry, at_top=False)
        self._update_activity_empty()

    def append_activity(self, entry: ActivityEntry, *, at_top: bool = True) -> None:
        t = theme.tokens()
        item = QListWidgetItem(f"{entry.timestamp:%H:%M:%S}    {entry.message}")
        if entry.level == "error":
            item.setIcon(fluent_icons.icon("error", t.critical, 14))
            item.setForeground(QColor(t.critical))
        elif entry.level == "warning":
            item.setIcon(fluent_icons.icon("warning", t.caution, 14))
            item.setForeground(QColor(t.caution))
        else:
            item.setIcon(fluent_icons.icon("info", t.text_tertiary, 14))
        if at_top:
            self._activity_list.insertItem(0, item)
        else:
            self._activity_list.addItem(item)
        while self._activity_list.count() > ACTIVITY_LIMIT:
            self._activity_list.takeItem(self._activity_list.count() - 1)
        self._update_activity_empty()

    def _update_activity_empty(self) -> None:
        empty = self._activity_list.count() == 0
        self._activity_empty.setVisible(empty)
        self._activity_list.setVisible(not empty)

    # -- window -------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self.visibilityChanged.emit(False)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Closing the window returns the app to the tray rather than quitting.
        event.ignore()
        self.hide()
