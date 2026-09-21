"""The full dashboard: profile cards, activity log, settings, and privacy."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .models import ActivityEntry, ProfileState
from .services.settings_service import Settings
from .widgets.profile_card import ProfileCard
from .widgets.theme import muted, muted_label_css

PRIVACY_STORED = [
    "UI preferences: refresh interval, notification thresholds, and the "
    "on/off state of startup and global shortcuts.",
    "The non-secret profile aliases (personal, work) used to address "
    "claude-swap.",
    "A capped local activity history of safe status lines, with email "
    "addresses masked and any token-shaped text redacted before writing.",
]

PRIVACY_NOT_STORED = [
    "No access tokens, refresh tokens, API keys, session cookies, or "
    "passwords - this app never reads or writes them.",
    "No contents of .claude\\.credentials.json, .claude-swap-backup\\, or any "
    "Claude Code session file.",
    "No raw output from claude-swap. Only parsed, redacted fields are kept.",
    "No telemetry, analytics, crash reporting, cloud sync, or remote "
    "configuration. The app makes no network connections of its own.",
]

PRIVACY_LIMITS = [
    "Switching changes the account Claude Code uses for new sessions. It does "
    "not sign your browser in or out of claude.ai.",
    "Nothing is ever terminated for you: Claude Code, VS Code, terminals, and "
    "open chats are left alone.",
    "All account data comes from claude-swap. This app calls no Anthropic API "
    "directly.",
]


class MainWindow(QMainWindow):
    """Tabbed window opened from the tray or the compact popup."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    refreshRequested = Signal()
    settingsChanged = Signal(object)  # Settings
    clearHistoryRequested = Signal()
    openDataFolderRequested = Signal()

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
        self.resize(880, 660)
        self._loading_settings = False
        self._cards: dict[str, ProfileCard] = {}

        tabs = QTabWidget()
        tabs.addTab(self._build_dashboard(states, mock_mode), "Dashboard")
        tabs.addTab(self._build_settings(settings), "Settings")
        tabs.addTab(self._build_privacy(data_dir), "Privacy")
        self.setCentralWidget(tabs)

    # -- dashboard ----------------------------------------------------------

    def _build_dashboard(self, states: tuple[ProfileState, ...], mock_mode: bool) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self._banner = QLabel("")
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(
            "background-color: rgba(245, 158, 11, 0.15); color: palette(text);"
            "border: 1px solid rgba(245, 158, 11, 0.4); border-radius: 8px; padding: 8px;"
        )
        self._banner.setVisible(False)
        layout.addWidget(self._banner)

        if mock_mode:
            demo = QLabel(
                "Demo mode - all figures below are synthetic. No real account "
                "is being read or changed."
            )
            demo.setWordWrap(True)
            demo.setStyleSheet(
                "background-color: rgba(59, 130, 246, 0.14);"
                "border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 8px; padding: 8px;"
            )
            layout.addWidget(demo)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)
        for state in states:
            card = ProfileCard(state, compact=False)
            card.switchRequested.connect(self.switchRequested.emit)
            card.launchRequested.connect(self.launchRequested.emit)
            card.reloginRequested.connect(self.reloginRequested.emit)
            cards_row.addWidget(card)
            self._cards[state.profile.key] = card
        layout.addLayout(cards_row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self._refresh_button = QPushButton("Refresh now")
        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        controls.addWidget(self._refresh_button)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"color: {muted(self)};")
        controls.addWidget(self._status_label)
        controls.addStretch(1)
        layout.addLayout(controls)

        log_group = QGroupBox("Activity")
        log_layout = QVBoxLayout(log_group)
        self._activity_list = QListWidget()
        self._activity_list.setAlternatingRowColors(True)
        self._activity_list.setStyleSheet("font-size: 12px;")
        log_layout.addWidget(self._activity_list)

        log_controls = QHBoxLayout()
        note = QLabel(
            "Only safe status lines are recorded. Emails are masked and "
            "token-shaped text is redacted before anything is written."
        )
        note.setWordWrap(True)
        note.setStyleSheet(muted_label_css(self))
        log_controls.addWidget(note, 1)
        clear_button = QPushButton("Clear history")
        clear_button.clicked.connect(self.clearHistoryRequested.emit)
        log_controls.addWidget(clear_button)
        log_layout.addLayout(log_controls)

        layout.addWidget(log_group, 1)
        return page

    # -- settings -----------------------------------------------------------

    def _build_settings(self, settings: Settings) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)

        polling = QGroupBox("Usage polling")
        polling_form = QFormLayout(polling)
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(30, 3600)
        self._interval_spin.setSingleStep(30)
        self._interval_spin.setSuffix(" seconds")
        self._interval_spin.setValue(settings.refresh_interval_seconds)
        self._interval_spin.valueChanged.connect(self._emit_settings)
        polling_form.addRow("Refresh interval", self._interval_spin)
        polling_form.addRow(
            QLabel(
                "Polls back off automatically after an error and never overlap."
            )
        )
        layout.addWidget(polling)

        startup = QGroupBox("Windows integration")
        startup_layout = QVBoxLayout(startup)
        self._autostart_check = QCheckBox("Launch Claude Profiles when I sign in to Windows")
        self._autostart_check.setChecked(settings.launch_at_signin)
        self._autostart_check.toggled.connect(self._emit_settings)
        startup_layout.addWidget(self._autostart_check)
        startup_note = QLabel(
            "Creates a shortcut in your Startup folder. No registry keys are written."
        )
        startup_note.setStyleSheet(muted_label_css(self))
        startup_layout.addWidget(startup_note)

        self._hotkeys_check = QCheckBox(
            "Enable global shortcuts (Ctrl+Alt+1 Personal, Ctrl+Alt+2 Work)"
        )
        self._hotkeys_check.setChecked(settings.hotkeys_enabled)
        self._hotkeys_check.toggled.connect(self._emit_settings)
        startup_layout.addWidget(self._hotkeys_check)
        self._hotkey_status = QLabel("")
        self._hotkey_status.setStyleSheet(muted_label_css(self))
        self._hotkey_status.setWordWrap(True)
        startup_layout.addWidget(self._hotkey_status)
        layout.addWidget(startup)

        notify = QGroupBox("Notifications")
        notify_form = QFormLayout(notify)
        self._notify_check = QCheckBox("Notify me when usage crosses a threshold")
        self._notify_check.setChecked(settings.notifications_enabled)
        self._notify_check.toggled.connect(self._emit_settings)
        notify_form.addRow(self._notify_check)

        self._warn_spin = QSpinBox()
        self._warn_spin.setRange(1, 99)
        self._warn_spin.setSuffix(" %")
        self._warn_spin.setValue(settings.warn_threshold_pct)
        self._warn_spin.valueChanged.connect(self._emit_settings)
        notify_form.addRow("Warning threshold", self._warn_spin)

        self._critical_spin = QSpinBox()
        self._critical_spin.setRange(2, 100)
        self._critical_spin.setSuffix(" %")
        self._critical_spin.setValue(settings.critical_threshold_pct)
        self._critical_spin.valueChanged.connect(self._emit_settings)
        notify_form.addRow("Critical threshold", self._critical_spin)
        notify_form.addRow(
            QLabel(
                "Confirmation of a switch you requested is always shown, "
                "regardless of this setting."
            )
        )
        layout.addWidget(notify)
        layout.addStretch(1)
        return page

    def _emit_settings(self) -> None:
        if self._loading_settings:
            return
        self.settingsChanged.emit(
            Settings(
                refresh_interval_seconds=self._interval_spin.value(),
                launch_at_signin=self._autostart_check.isChecked(),
                notifications_enabled=self._notify_check.isChecked(),
                warn_threshold_pct=self._warn_spin.value(),
                critical_threshold_pct=self._critical_spin.value(),
                hotkeys_enabled=self._hotkeys_check.isChecked(),
            )
        )

    def load_settings(self, settings: Settings) -> None:
        """Push values back into the controls without re-emitting."""
        self._loading_settings = True
        try:
            self._interval_spin.setValue(settings.refresh_interval_seconds)
            self._autostart_check.setChecked(settings.launch_at_signin)
            self._notify_check.setChecked(settings.notifications_enabled)
            self._warn_spin.setValue(settings.warn_threshold_pct)
            self._critical_spin.setValue(settings.critical_threshold_pct)
            self._hotkeys_check.setChecked(settings.hotkeys_enabled)
        finally:
            self._loading_settings = False

    def set_hotkey_status(self, message: str) -> None:
        self._hotkey_status.setText(message)

    # -- privacy ------------------------------------------------------------

    def _build_privacy(self, data_dir: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "<b>Claude Profiles runs entirely on this machine.</b> Its only "
            "outside interaction is running the local <code>cswap</code> "
            "command and reading the JSON it prints."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for title, items in (
            ("What this app stores", PRIVACY_STORED),
            ("What it never stores", PRIVACY_NOT_STORED),
            ("What it cannot do", PRIVACY_LIMITS),
        ):
            group = QGroupBox(title)
            group_layout = QVBoxLayout(group)
            for item in items:
                label = QLabel(f"• {item}")
                label.setWordWrap(True)
                group_layout.addWidget(label)
            layout.addWidget(group)

        location = QLabel(f"Data folder: <code>{data_dir}</code>")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(location)

        buttons = QHBoxLayout()
        open_button = QPushButton("Open data folder")
        open_button.clicked.connect(self.openDataFolderRequested.emit)
        buttons.addWidget(open_button)
        clear_button = QPushButton("Clear local activity history")
        clear_button.clicked.connect(self.clearHistoryRequested.emit)
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(1)
        return page

    # -- updates ------------------------------------------------------------

    def update_states(self, states: tuple[ProfileState, ...]) -> None:
        for state in states:
            card = self._cards.get(state.profile.key)
            if card is not None:
                card.set_state(state)

    def set_busy(self, busy: bool) -> None:
        for card in self._cards.values():
            card.set_busy(busy)
        self._refresh_button.setEnabled(not busy)
        self._refresh_button.setText("Refreshing…" if busy else "Refresh now")

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def show_banner(self, text: str) -> None:
        self._banner.setText(text)
        self._banner.setVisible(bool(text))

    def set_activity(self, entries: list[ActivityEntry]) -> None:
        self._activity_list.clear()
        for entry in reversed(entries):
            self.append_activity(entry, at_top=False)

    def append_activity(self, entry: ActivityEntry, *, at_top: bool = True) -> None:
        item = QListWidgetItem(f"{entry.timestamp:%H:%M:%S}   {entry.message}")
        if entry.level == "error":
            item.setForeground(Qt.GlobalColor.red)
        elif entry.level == "warning":
            item.setForeground(Qt.GlobalColor.darkYellow)
        if at_top:
            self._activity_list.insertItem(0, item)
        else:
            self._activity_list.addItem(item)
        while self._activity_list.count() > 400:
            self._activity_list.takeItem(self._activity_list.count() - 1)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # Closing the window returns the app to the tray rather than quitting.
        event.ignore()
        self.hide()


class Separator(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Sunken)
