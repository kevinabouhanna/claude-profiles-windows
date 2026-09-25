"""The per-profile card shown in both the tray popup and the full dashboard."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..models import ProfileState
from ..resources import fluent_icons
from . import theme
from .status_badge import ProfileAvatar, StatusBadge, format_age, health_badge_text
from .usage_bar import UsageBar

MAX_SCOPED_ROWS = 2

# Profile key -> Fluent glyph, so Personal and Work are distinguishable at a
# glance without relying on colour alone.
PROFILE_GLYPHS = {"personal": "personal", "work": "work"}


class Divider(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(1)
        self.setStyleSheet(f"background-color: {theme.tokens().divider}; border: none;")


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

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        root.addLayout(self._build_header(state))
        root.addWidget(Divider())

        # Registered and unregistered profiles show entirely different content,
        # so they are separate pages rather than one layout with hidden rows.
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_usage_page())
        self._stack.addWidget(self._build_empty_page(state))
        root.addWidget(self._stack)

        root.addStretch(1)
        root.addLayout(self._build_footer())
        root.addLayout(self._build_actions(state))

        self.set_state(state)

    # -- construction -------------------------------------------------------

    def _build_header(self, state: ProfileState) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(10)

        self._avatar = ProfileAvatar(
            self._accent,
            PROFILE_GLYPHS.get(state.profile.key, "personal"),
            size=34 if not self._compact else 30,
        )
        header.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignTop)

        identity = QVBoxLayout()
        identity.setSpacing(1)

        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self._name_label = QLabel(state.profile.name)
        self._name_label.setStyleSheet(
            theme.text_css(theme.BODY if self._compact else theme.BODY_LARGE, "primary", 600)
        )
        name_row.addWidget(self._name_label)
        self._active_badge = StatusBadge("Active", "accent")
        self._active_badge.setVisible(False)
        name_row.addWidget(self._active_badge)
        name_row.addStretch(1)
        identity.addLayout(name_row)

        self._email_label = QLabel("")
        self._email_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        self._email_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        identity.addWidget(self._email_label)

        header.addLayout(identity, 1)
        return header

    def _build_usage_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._five_bar = UsageBar("5h", self._accent, compact=self._compact)
        self._seven_bar = UsageBar("7d", self._accent, compact=self._compact)
        layout.addWidget(self._five_bar)
        layout.addWidget(self._seven_bar)

        self._scoped_container = QVBoxLayout()
        self._scoped_container.setSpacing(2)
        layout.addLayout(self._scoped_container)

        self._spend_label = QLabel("")
        self._spend_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        self._spend_label.setVisible(False)
        layout.addWidget(self._spend_label)

        self._pace_label = QLabel("")
        self._pace_label.setWordWrap(True)
        self._pace_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        self._pace_label.setVisible(False)
        layout.addWidget(self._pace_label)
        return page

    def _build_empty_page(self, state: ProfileState) -> QWidget:
        """Shown before a profile is registered.

        Empty progress bars would imply a reading of zero, which is not what
        "not set up" means, so the card explains the state instead.
        """
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(
            fluent_icons.pixmap("add_account", theme.tokens().text_tertiary, 20)
        )
        icon.setFixedWidth(22)
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

        message = QLabel(
            f"{state.profile.name} isn't set up yet. Sign in to Claude Code as "
            "this account and register it to see usage here."
        )
        message.setWordWrap(True)
        message.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        row.addWidget(message, 1)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self._health_badge = StatusBadge("", "muted")
        row.addWidget(self._health_badge)
        self._freshness_label = QLabel("")
        self._freshness_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        row.addWidget(self._freshness_label)
        row.addStretch(1)
        return row

    def _build_actions(self, state: ProfileState) -> QVBoxLayout:
        wrapper = QVBoxLayout()
        wrapper.setSpacing(8)

        self._relogin_button = QPushButton("  Fix sign-in")
        self._relogin_button.setIcon(
            fluent_icons.icon("shield", theme.tokens().critical, 16)
        )
        self._relogin_button.setVisible(False)
        self._relogin_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._relogin_button.setStyleSheet(theme.standard_button_css())
        self._relogin_button.clicked.connect(lambda: self.reloginRequested.emit(self._key))
        wrapper.addWidget(self._relogin_button)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self._switch_button = QPushButton()
        self._switch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._switch_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._switch_button.setStyleSheet(theme.accent_button_css(self._accent))
        self._switch_button.clicked.connect(self._on_primary_clicked)
        actions.addWidget(self._switch_button, 1)

        self._launch_button = QPushButton()
        self._launch_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._launch_button.setStyleSheet(theme.standard_button_css())
        self._launch_button.setIcon(
            fluent_icons.icon("terminal", theme.tokens().text, 16)
        )
        self._launch_button.setFixedWidth(40)
        self._launch_button.clicked.connect(lambda: self.launchRequested.emit(self._key))
        actions.addWidget(self._launch_button, 0)

        wrapper.addLayout(actions)
        return wrapper

    # -- styling ------------------------------------------------------------

    def _apply_card_style(self, *, active: bool) -> None:
        t = theme.tokens()
        border = t.on_surface(self._accent) if active else t.card_stroke
        self.setStyleSheet(theme.card_css("profileCard", border=border))

    # -- state --------------------------------------------------------------

    def set_state(self, state: ProfileState) -> None:
        self._state = state
        account = state.account
        t = theme.tokens()

        self._email_label.setText(account.email if account else "Not registered")
        self._active_badge.setVisible(state.is_active)
        self._apply_card_style(active=state.is_active)
        self._stack.setCurrentIndex(0 if account is not None else 1)

        if account is not None:
            usage = account.effective_usage
            stale = account.is_stale
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
        self._switch_button.setIcon(self._primary_icon(state))
        self._launch_button.setEnabled(bool(account and account.number is not None))
        self._launch_button.setToolTip(self._launch_tooltip(state))
        self._launch_button.setIcon(
            fluent_icons.icon(
                "terminal",
                t.text if self._launch_button.isEnabled() else t.text_disabled,
                16,
            )
        )

    def _on_primary_clicked(self) -> None:
        # An unregistered profile has nothing to switch to, so the same button
        # becomes the way into setup rather than a dead control.
        if self._state.account is None:
            self.setupRequested.emit(self._key)
        else:
            self.switchRequested.emit(self._key)

    def _primary_text(self, state: ProfileState) -> str:
        if state.account is None:
            return f"  Set up {state.profile.name}"
        return "  Active profile" if state.is_active else f"  Switch to {state.profile.name}"

    def _primary_icon(self, state: ProfileState):
        t = theme.tokens()
        if state.account is None:
            return fluent_icons.icon("add", t.text_on_accent, 16)
        if state.is_active:
            return fluent_icons.icon("success", t.text_disabled, 16)
        return fluent_icons.icon("switch", t.text_on_accent, 16)

    def _primary_enabled(self, state: ProfileState) -> bool:
        if state.account is None:
            return True
        return not state.is_active

    def _freshness_text(self, state: ProfileState) -> str:
        account = state.account
        if account is None:
            return ""
        if account.effective_usage is None:
            return ""
        if account.usage_error and account.usage_retry_at:
            return (
                f"Updated {format_age(account.effective_age_seconds)} · "
                f"retry {account.usage_retry_at.astimezone():%H:%M}"
            )
        return f"Updated {format_age(account.effective_age_seconds)}"

    def _launch_tooltip(self, state: ProfileState) -> str:
        if state.account is None:
            return "Set this profile up before launching a session for it."
        if state.is_active:
            return (
                "Open a terminal running Claude Code.\n"
                "This profile is already the active login, so the session uses "
                "it directly rather than an isolated one."
            )
        return (
            "Open an isolated terminal session for this account.\n"
            "The globally active profile is unchanged."
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
        text = f"Extra usage {spend.currency} {spend.used:.2f}"
        if spend.limit:
            text += f" of {spend.limit:.2f}"
        self._spend_label.setText(text)
        self._spend_label.setVisible(True)

    def _render_pace(self, usage) -> None:
        if self._compact or usage is None or usage.seven_day is None:
            self._pace_label.setVisible(False)
            return
        seven = usage.seven_day
        parts: list[str] = []
        if seven.expected_pct is not None and seven.ahead_of_pace is not None:
            direction = "Ahead of" if seven.ahead_of_pace else "Within"
            parts.append(f"{direction} pace, expected {seven.expected_pct:.0f}%")
        if seven.will_last_to_reset is False:
            parts.append("may run out before reset")
        if not parts:
            self._pace_label.setVisible(False)
            return
        self._pace_label.setText(" · ".join(parts))
        self._pace_label.setVisible(True)

    def set_busy(self, busy: bool) -> None:
        """Disable the switch control while an action is in flight."""
        state = self._state
        self._switch_button.setEnabled(not busy and self._primary_enabled(state))
        self._launch_button.setEnabled(
            not busy and bool(state.account and state.account.number is not None)
        )
        self._switch_button.setText(
            "  Working…" if busy else self._primary_text(state)
        )
