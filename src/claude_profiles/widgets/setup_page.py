"""In-app account setup.

Registering an account with claude-swap is a two-part job and only the second
part belongs in a GUI:

1. **Signing in** is an interactive OAuth flow that Claude Code owns. The app
   opens a terminal and steps back; it never handles credentials or drives the
   login.
2. **Registering** the account that is *already* signed in is a single
   ``cswap add --alias <alias>`` call, which this page runs on a click.

Because ``add`` captures whichever account Claude Code is currently signed in
as, the page always shows that address first. Clicking "register" without
knowing who you are signed in as is how you end up with Work stored under the
Personal alias, so the identity is shown directly above the button.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import AccountList, ActiveStatus, Profile, ProfileState
from .status_badge import StatusBadge
from .theme import hairline, muted_label_css


class StepBox(QGroupBox):
    """A numbered setup step."""

    def __init__(self, number: int, title: str, detail: str) -> None:
        super().__init__(f"Step {number} — {title}")
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self.detail = QLabel(detail)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(muted_label_css(self, 12))
        layout.addWidget(self.detail)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout.addLayout(self.body)


class SetupPage(QWidget):
    """Guides the user through registering both profiles."""

    signInRequested = Signal()
    registerRequested = Signal(str)  # profile key
    assignAliasRequested = Signal(int, str)  # account number, profile key
    refreshRequested = Signal()

    def __init__(self, profiles: tuple[Profile, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._profiles = profiles
        self._accounts: AccountList | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        intro = QLabel(
            "Claude Profiles does not handle sign-in itself. Claude Code signs "
            "you in, and claude-swap stores the result — this page just drives "
            "those two steps in the right order."
        )
        intro.setWordWrap(True)
        outer.addWidget(intro)

        outer.addWidget(self._build_current_login())
        outer.addWidget(self._build_sign_in_step())
        outer.addWidget(self._build_register_step())
        outer.addWidget(self._build_existing_accounts())
        outer.addStretch(1)

        self._result = QLabel("")
        self._result.setWordWrap(True)
        self._result.setStyleSheet(muted_label_css(self, 12))
        outer.addWidget(self._result)

    # -- sections -----------------------------------------------------------

    def _build_current_login(self) -> QWidget:
        box = QGroupBox("Currently signed in to Claude Code")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self._login_email = QLabel("Checking…")
        self._login_email.setStyleSheet("font-size: 14px; font-weight: 600;")
        self._login_email.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row.addWidget(self._login_email)
        row.addSpacing(8)
        self._login_badge = StatusBadge("", "muted")
        row.addWidget(self._login_badge)
        row.addStretch(1)
        recheck = QPushButton("Re-check")
        recheck.setCursor(Qt.CursorShape.PointingHandCursor)
        recheck.clicked.connect(self.refreshRequested.emit)
        row.addWidget(recheck)
        layout.addLayout(row)

        self._login_hint = QLabel("")
        self._login_hint.setWordWrap(True)
        self._login_hint.setStyleSheet(muted_label_css(self, 12))
        layout.addWidget(self._login_hint)
        return box

    def _build_sign_in_step(self) -> QWidget:
        step = StepBox(
            1,
            "Sign in as the account you want to add",
            "Opens a terminal running Claude Code. If it signs you in as the "
            "wrong account, type /login there to switch. Close the terminal "
            "when you are done, then press Re-check above.",
        )
        button = QPushButton("Open a terminal to sign in to Claude Code")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.signInRequested.emit)
        step.body.addWidget(button)
        self._sign_in_button = button
        return step

    def _build_register_step(self) -> QWidget:
        step = StepBox(
            2,
            "Store the signed-in account under a profile",
            "This runs cswap add for the address shown above. Check it is the "
            "right one before choosing a profile.",
        )
        self._register_buttons: dict[str, QPushButton] = {}
        self._register_status: dict[str, StatusBadge] = {}

        for profile in self._profiles:
            row = QHBoxLayout()
            row.setSpacing(8)

            swatch = QFrame()
            swatch.setFixedSize(4, 26)
            swatch.setStyleSheet(f"background-color: {profile.color}; border-radius: 2px;")
            row.addWidget(swatch)

            name = QLabel(profile.name)
            name.setStyleSheet("font-weight: 600;")
            name.setFixedWidth(70)
            row.addWidget(name)

            badge = StatusBadge("Not set up", "muted")
            self._register_status[profile.key] = badge
            row.addWidget(badge)
            row.addStretch(1)

            button = QPushButton(f"Register as {profile.name}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background-color: {profile.color}; color: white;"
                "border: none; border-radius: 6px; padding: 6px 12px; font-weight: 600; }"
                f"QPushButton:disabled {{ background-color: {hairline(self)}; }}"
            )
            button.clicked.connect(
                lambda _=False, key=profile.key: self.registerRequested.emit(key)
            )
            self._register_buttons[profile.key] = button
            row.addWidget(button)

            container = QWidget()
            container.setLayout(row)
            step.body.addWidget(container)

        return step

    def _build_existing_accounts(self) -> QWidget:
        box = QGroupBox("Accounts claude-swap already knows about")
        layout = QVBoxLayout(box)
        layout.setSpacing(8)

        self._accounts_label = QLabel("None yet.")
        self._accounts_label.setWordWrap(True)
        self._accounts_label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._accounts_label)

        note = QLabel(
            "If an account is already registered but carries the wrong alias, "
            "point it at a profile here instead of adding it again."
        )
        note.setWordWrap(True)
        note.setStyleSheet(muted_label_css(self, 12))
        layout.addWidget(note)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._alias_account = QComboBox()
        self._alias_account.setMinimumWidth(240)
        row.addWidget(self._alias_account, 1)

        row.addWidget(QLabel("→"))

        self._alias_profile = QComboBox()
        for profile in self._profiles:
            self._alias_profile.addItem(profile.name, profile.key)
        row.addWidget(self._alias_profile)

        self._alias_button = QPushButton("Set alias")
        self._alias_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._alias_button.clicked.connect(self._emit_alias)
        row.addWidget(self._alias_button)
        layout.addLayout(row)
        return box

    def _emit_alias(self) -> None:
        number = self._alias_account.currentData()
        key = self._alias_profile.currentData()
        if number is not None and key:
            self.assignAliasRequested.emit(int(number), str(key))

    # -- updates ------------------------------------------------------------

    def update_login(self, status: ActiveStatus | None) -> None:
        if status is None or not status.email:
            self._login_email.setText("Not signed in")
            self._login_badge.apply("Unknown", "warn")
            self._login_hint.setText(
                "claude-swap could not report a current login. Sign in with "
                "Claude Code, then press Re-check."
            )
            self._set_register_enabled(False)
            return

        self._login_email.setText(status.email)
        if status.managed:
            self._login_badge.apply("Already registered", "ok")
            self._login_hint.setText(
                "This account is already stored by claude-swap. To add your "
                "other account, sign in as it first using Step 1."
            )
        else:
            self._login_badge.apply("Not yet registered", "warn")
            self._login_hint.setText(
                "This account is signed in but not stored by claude-swap. "
                "Register it below to use it as a profile."
            )
        self._set_register_enabled(True)

    def update_accounts(
        self, accounts: AccountList | None, states: tuple[ProfileState, ...]
    ) -> None:
        self._accounts = accounts

        for state in states:
            badge = self._register_status.get(state.profile.key)
            button = self._register_buttons.get(state.profile.key)
            if badge is None or button is None:
                continue
            if state.account is not None:
                badge.apply(state.account.email, "ok")
                button.setText(f"Replace {state.profile.name}")
            else:
                badge.apply("Not set up", "muted")
                button.setText(f"Register as {state.profile.name}")

        rows: list[str] = []
        self._alias_account.clear()
        if accounts is not None:
            for account in accounts.accounts:
                alias = f" · alias <b>{account.alias}</b>" if account.alias else ""
                active = " · active" if account.active else ""
                rows.append(f"{account.number}. {account.email}{alias}{active}")
                if account.number is not None:
                    label = f"{account.number}. {account.email}"
                    if account.alias:
                        label += f"  (alias: {account.alias})"
                    self._alias_account.addItem(label, account.number)

        self._accounts_label.setText("<br>".join(rows) if rows else "None yet.")
        has_accounts = bool(rows)
        self._alias_account.setEnabled(has_accounts)
        self._alias_profile.setEnabled(has_accounts)
        self._alias_button.setEnabled(has_accounts)

    def _set_register_enabled(self, enabled: bool) -> None:
        for button in self._register_buttons.values():
            button.setEnabled(enabled)

    def set_busy(self, busy: bool) -> None:
        self._sign_in_button.setEnabled(not busy)
        self._alias_button.setEnabled(not busy and self._alias_account.count() > 0)
        for button in self._register_buttons.values():
            button.setEnabled(not busy)

    def set_result(self, message: str, ok: bool = True) -> None:
        color = "#16a34a" if ok else "#dc2626"
        self._result.setStyleSheet(f"font-size: 12px; color: {color}; font-weight: 600;")
        self._result.setText(message)
