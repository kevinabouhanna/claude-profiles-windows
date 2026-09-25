"""The per-profile card shown in both the tray popup and the full dashboard."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..models import ProfileState
from .status_badge import StatusBadge, format_age, health_badge_text
from .theme import faint, hairline, muted_label_css
from .usage_bar import UsageBar

MAX_SCOPED_ROWS = 3


class ProfileCard(QFrame):
    """Renders one profile: identity, quota windows, health, and actions."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    setupRequested = Signal(str)

    def __init__(
        self, state: ProfileState, *, compact: bool = False, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._compact = compact
        self._key = state.profile.key
        self._accent = state.profile.color
        self._scoped_bars: list[UsageBar] = []

        self.setObjectName("profileCard")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._apply_card_style(active=False)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._accent_bar = QWidget(self)
        self._accent_bar.setFixedWidth(4)
        self._accent_bar.setStyleSheet(
            f"background-color: {self._accent}; border-top-left-radius: 10px;"
            "border-bottom-left-radius: 10px;"
        )
        outer.addWidget(self._accent_bar)

        body = QVBoxLayout()
        body.setContentsMargins(14, 12, 14, 12)
        body.setSpacing(7 if compact else 9)
        outer.addLayout(body, 1)

        # -- header
        header = QHBoxLayout()
        header.setSpacing(8)
        self._name_label = QLabel(state.profile.name)
        name_size = 13 if compact else 15
        self._name_label.setStyleSheet(f"font-size: {name_size}px; font-weight: 700;")
        header.addWidget(self._name_label)

        self._active_badge = StatusBadge("ACTIVE", "ok")
        self._active_badge.setVisible(False)
        header.addStretch(1)
        header.addWidget(self._active_badge)
        body.addLayout(header)

        self._email_label = QLabel("")
        self._email_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._email_label.setStyleSheet(muted_label_css(self))
        body.addWidget(self._email_label)

        # -- quota windows
        self._five_bar = UsageBar("5h", self._accent, compact=compact)
        self._seven_bar = UsageBar("7d", self._accent, compact=compact)
        body.addWidget(self._five_bar)
        body.addWidget(self._seven_bar)

        self._scoped_container = QVBoxLayout()
        self._scoped_container.setSpacing(2)
        body.addLayout(self._scoped_container)

        self._spend_label = QLabel("")
        self._spend_label.setStyleSheet(muted_label_css(self))
        self._spend_label.setVisible(False)
        body.addWidget(self._spend_label)

        self._pace_label = QLabel("")
        self._pace_label.setWordWrap(True)
        self._pace_label.setStyleSheet(muted_label_css(self))
        self._pace_label.setVisible(False)
        body.addWidget(self._pace_label)

        # -- health
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self._health_badge = StatusBadge("", "muted")
        status_row.addWidget(self._health_badge)
        self._freshness_label = QLabel("")
        self._freshness_label.setStyleSheet(muted_label_css(self))
        status_row.addWidget(self._freshness_label)
        status_row.addStretch(1)
        body.addLayout(status_row)

        self._relogin_button = QPushButton("Open re-login instructions")
        self._relogin_button.setVisible(False)
        self._relogin_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._relogin_button.clicked.connect(lambda: self.reloginRequested.emit(self._key))
        body.addWidget(self._relogin_button)

        # -- actions
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._switch_button = QPushButton(f"Switch to {state.profile.name}")
        self._switch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._switch_button.setStyleSheet(self._primary_button_style())
        self._switch_button.clicked.connect(self._on_primary_clicked)
        actions.addWidget(self._switch_button, 2)

        self._launch_button = QPushButton("Launch")
        self._launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_button.clicked.connect(lambda: self.launchRequested.emit(self._key))
        actions.addWidget(self._launch_button, 1)
        body.addLayout(actions)

        self.set_state(state)

    # -- styling ------------------------------------------------------------

    def _apply_card_style(self, *, active: bool) -> None:
        border = self._accent if active else hairline(self)
        self.setStyleSheet(
            f"#profileCard {{ background-color: palette(base); border: 1px solid {border};"
            "border-radius: 10px; }"
        )

    def _primary_button_style(self) -> str:
        return (
            f"QPushButton {{ background-color: {self._accent}; color: white; border: none;"
            "border-radius: 6px; padding: 7px 12px; font-weight: 600; }"
            f"QPushButton:disabled {{ background-color: {hairline(self)};"
            f" color: {faint(self)}; }}"
        )

    # -- state --------------------------------------------------------------

    def set_state(self, state: ProfileState) -> None:
        self._state = state
        account = state.account

        self._email_label.setText(
            account.email if account else "not registered with claude-swap"
        )
        self._active_badge.setVisible(state.is_active)
        self._apply_card_style(active=state.is_active)

        usage = account.effective_usage if account else None
        stale = bool(account and account.is_stale)

        self._five_bar.set_window(usage.five_hour if usage else None, stale=stale)
        self._seven_bar.set_window(usage.seven_day if usage else None, stale=stale)
        self._render_scoped(usage.scoped if usage else (), stale)
        self._render_spend(usage)
        self._render_pace(usage)

        text, tone = health_badge_text(account)
        self._health_badge.apply(text, tone)
        self._relogin_button.setVisible(state.needs_reauth)

        self._freshness_label.setText(self._freshness_text(state))
        self._switch_button.setEnabled(self._primary_enabled(state))
        self._switch_button.setText(self._primary_text(state))
        self._launch_button.setEnabled(bool(account and account.number is not None))
        self._launch_button.setToolTip(self._launch_tooltip(state))

    def _on_primary_clicked(self) -> None:
        # An unregistered profile has nothing to switch to, so the same button
        # becomes the way into setup rather than a dead control.
        if self._state.account is None:
            self.setupRequested.emit(self._key)
        else:
            self.switchRequested.emit(self._key)

    def _primary_text(self, state: ProfileState) -> str:
        if state.account is None:
            return f"Set up {state.profile.name}…"
        return "Active" if state.is_active else f"Switch to {state.profile.name}"

    def _primary_enabled(self, state: ProfileState) -> bool:
        if state.account is None:
            return True
        return not state.is_active

    def _freshness_text(self, state: ProfileState) -> str:
        account = state.account
        if account is None:
            return f"Add it with: cswap add --alias {state.profile.alias}"
        if account.effective_usage is None:
            # No reading at all: the health badge already says why, so avoid
            # an empty "updated unknown age".
            return "no usage data yet"
        if account.usage_error:
            retry = ""
            if account.usage_retry_at:
                retry = f"; retrying at {account.usage_retry_at.astimezone():%H:%M}"
            return f"updated {format_age(account.effective_age_seconds)}{retry}"
        return f"updated {format_age(account.effective_age_seconds)}"

    def _launch_tooltip(self, state: ProfileState) -> str:
        if state.is_active:
            return (
                "Opens a new terminal running Claude Code.\n"
                "This profile is already the active login, so the session uses it "
                "directly rather than an isolated one."
            )
        return (
            "Opens an isolated terminal session for this account.\n"
            "It does not change the globally active profile."
        )

    def _render_scoped(self, scoped: tuple, stale: bool) -> None:
        rows = list(scoped)[:MAX_SCOPED_ROWS]
        while len(self._scoped_bars) < len(rows):
            bar = UsageBar("model", self._accent, compact=True)
            self._scoped_container.addWidget(bar)
            self._scoped_bars.append(bar)
        for index, bar in enumerate(self._scoped_bars):
            if index < len(rows):
                bar.set_window(rows[index], stale=stale)
                bar.setVisible(True)
            else:
                bar.setVisible(False)

    def _render_spend(self, usage) -> None:
        spend = usage.spend if usage else None
        if spend is None:
            self._spend_label.setVisible(False)
            return
        text = f"Extra usage: {spend.currency} {spend.used:.2f}"
        if spend.limit:
            text += f" of {spend.limit:.2f}"
        if spend.countdown:
            text += f" · resets in {spend.countdown}"
        self._spend_label.setText(text)
        self._spend_label.setVisible(True)

    def _render_pace(self, usage) -> None:
        if self._compact or usage is None or usage.seven_day is None:
            self._pace_label.setVisible(False)
            return
        seven = usage.seven_day
        parts: list[str] = []
        if seven.expected_pct is not None and seven.ahead_of_pace is not None:
            direction = "ahead of" if seven.ahead_of_pace else "within"
            parts.append(f"{direction} pace (expected {seven.expected_pct:.0f}%)")
        if seven.will_last_to_reset is False:
            parts.append("projected to run out before reset")
        if not parts:
            self._pace_label.setVisible(False)
            return
        self._pace_label.setText("7-day: " + " · ".join(parts))
        self._pace_label.setVisible(True)

    def set_busy(self, busy: bool) -> None:
        """Disable the switch control while an action is in flight."""
        state = self._state
        self._switch_button.setEnabled(not busy and self._primary_enabled(state))
        self._launch_button.setEnabled(
            not busy and bool(state.account and state.account.number is not None)
        )
        self._switch_button.setText(
            "Working…" if busy else self._primary_text(state)
        )
