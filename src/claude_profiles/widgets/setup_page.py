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
knowing who you are signed in as is how you end up with one account stored
under another's name, so the identity is shown directly above the button.

There is no fixed list of profiles: every account registered here becomes one,
named by the alias typed in.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import AccountList, ActiveStatus, ProfileState, profile_name
from ..services.profile_service import ProfileService
from . import theme
from .status_badge import StatusBadge
from .theme import muted_label_css

ALIAS_PLACEHOLDER = "e.g. personal, work, client-acme"


class StepBox(QGroupBox):
    """A setup step. The number is dropped once there is nothing to set up."""

    def __init__(self, number: int, title: str, detail: str) -> None:
        super().__init__(f"Step {number} — {title}")
        self._number = number
        self._plain_title = title
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        self.detail = QLabel(detail)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(muted_label_css(self, 12))
        layout.addWidget(self.detail)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        layout.addLayout(self.body)

    def set_numbered(self, numbered: bool, title: str | None = None) -> None:
        """Show or hide the step number.

        Numbered steps imply an unfinished sequence. Once an account is
        connected this page is for adding or changing one, not completing
        setup, so the numbering would be misleading.
        """
        text = title or self._plain_title
        self.setTitle(f"Step {self._number} — {text}" if numbered else text)

    def set_detail(self, text: str) -> None:
        self.detail.setText(text)


class SetupPage(QWidget):
    """Guides the user through registering as many accounts as they have."""

    signInRequested = Signal()
    registerRequested = Signal(str)  # alias
    assignAliasRequested = Signal(int, str)  # account number, alias
    refreshRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._accounts: AccountList | None = None
        self._login: ActiveStatus | None = None
        self._busy = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)

        self._intro = QLabel(
            "Claude Profiles does not handle sign-in itself. Claude Code signs "
            "you in, and claude-swap stores the result."
        )
        self._intro.setWordWrap(True)
        outer.addWidget(self._intro)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setVisible(False)
        outer.addWidget(self._summary)

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
        self._signin_step = step
        return step

    def _build_register_step(self) -> QWidget:
        self._register_step = step = StepBox(
            2,
            "Name it and register it",
            "This runs cswap add for the address shown above, under the name you "
            "choose. Check the address before registering.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self._alias_input = QLineEdit()
        self._alias_input.setPlaceholderText(ALIAS_PLACEHOLDER)
        self._alias_input.setMaxLength(32)
        self._alias_input.textChanged.connect(self._sync_register_button)
        self._alias_input.returnPressed.connect(self._emit_register)
        row.addWidget(self._alias_input, 1)

        self._register_button = QPushButton("Register")
        self._register_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._register_button.setStyleSheet(theme.accent_button_css())
        self._register_button.clicked.connect(self._emit_register)
        row.addWidget(self._register_button)
        step.body.addLayout(row)

        self._alias_hint = QLabel("")
        self._alias_hint.setWordWrap(True)
        self._alias_hint.setStyleSheet(muted_label_css(self, 12))
        step.body.addWidget(self._alias_hint)
        self._sync_register_button()
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
            "To rename an account, or give a name to one registered without "
            "one, pick it here rather than adding it again."
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

        self._rename_input = QLineEdit()
        self._rename_input.setPlaceholderText("new name")
        self._rename_input.setMaxLength(32)
        self._rename_input.returnPressed.connect(self._emit_alias)
        row.addWidget(self._rename_input)

        self._alias_button = QPushButton("Rename")
        self._alias_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._alias_button.clicked.connect(self._emit_alias)
        row.addWidget(self._alias_button)
        layout.addLayout(row)
        return box

    def _typed_alias(self) -> str:
        return self._alias_input.text().strip().lower()

    def _sync_register_button(self, *_args) -> None:
        alias = self._typed_alias()
        problem = ProfileService.alias_problem(alias) if alias else None
        signed_in = bool(self._login and self._login.email)
        self._register_button.setEnabled(
            signed_in and bool(alias) and problem is None and not self._busy
        )
        if problem:
            self._alias_hint.setText(problem)
        elif alias:
            self._alias_hint.setText(f"It will appear as {profile_name(alias, None)}.")
        else:
            self._alias_hint.setText(
                "Any short name works. It becomes the account's label here and "
                "its alias in claude-swap."
            )

    def _emit_register(self) -> None:
        if self._register_button.isEnabled():
            self.registerRequested.emit(self._typed_alias())

    def _emit_alias(self) -> None:
        number = self._alias_account.currentData()
        alias = self._rename_input.text().strip().lower()
        if number is None:
            return
        problem = ProfileService.alias_problem(alias)
        if problem:
            self.set_result(problem, ok=False)
            return
        self.assignAliasRequested.emit(int(number), alias)

    # -- updates ------------------------------------------------------------

    def update_login(self, status: ActiveStatus | None) -> None:
        if status is None or not status.email:
            self._login_email.setText("Not signed in")
            self._login_badge.apply("Unknown", "warn")
            self._login_hint.setText(
                "claude-swap could not report a current login. Sign in with "
                "Claude Code, then press Re-check."
            )
            self._login = None
            self._sync_register_button()
            return

        self._login = status
        self._login_email.setText(status.email)
        if status.managed:
            self._login_badge.apply("Already registered", "ok")
            self._login_hint.setText(
                "This account is already stored by claude-swap. Registering it "
                "again under a new name renames it. To add a different account, "
                "sign in as it below first."
            )
        else:
            self._login_badge.apply("Not yet registered", "warn")
            self._login_hint.setText(
                "This account is signed in but not stored by claude-swap. Give "
                "it a name below to add it."
            )
        self._sync_register_button()

    def update_accounts(
        self, accounts: AccountList | None, states: tuple[ProfileState, ...]
    ) -> None:
        self._accounts = accounts

        rows: list[str] = []
        self._alias_account.clear()
        if accounts is not None:
            for account in accounts.accounts:
                # The label renders rich text, so anything taken from cswap
                # has to be escaped or an address containing "<" would be
                # swallowed as markup.
                email = html.escape(account.email)
                alias = (
                    f" · alias <b>{html.escape(account.alias)}</b>"
                    if account.alias
                    else ""
                )
                active = " · active" if account.active else ""
                rows.append(f"{account.number}. {email}{alias}{active}")
                if account.number is not None:
                    label = f"{account.number}. {account.email}"
                    if account.alias:
                        label += f"  (alias: {account.alias})"
                    self._alias_account.addItem(label, account.number)

        self._apply_framing(states)
        self._accounts_label.setText("<br>".join(rows) if rows else "None yet.")
        has_accounts = bool(rows)
        self._alias_account.setEnabled(has_accounts)
        self._rename_input.setEnabled(has_accounts)
        self._alias_button.setEnabled(has_accounts and not self._busy)

    def _apply_framing(self, states: tuple[ProfileState, ...]) -> None:
        """Present setup steps only when there is setup left to do."""
        t = theme.tokens()
        connected = [s for s in states if s.account is not None]

        if connected:
            names = ", ".join(s.profile.name for s in connected)
            count = len(connected)
            noun = "account is" if count == 1 else "accounts are"
            self._summary.setText(
                f"✓ {count} {noun} connected: {names}. Use this page to add "
                "another one or rename one."
            )
            self._summary.setStyleSheet(
                f"font-size: {theme.CAPTION}px; color: {t.success}; font-weight: 600;"
            )
            self._summary.setVisible(True)
            self._intro.setVisible(False)
            self._signin_step.set_numbered(False, "Sign in as another account")
            self._signin_step.set_detail(
                "Opens a terminal and your browser. If it signs you in as an "
                "account you already have, type /login there to switch."
            )
            self._register_step.set_numbered(False, "Name it and register it")
            self._register_step.set_detail(
                "Adds the account shown above under the name you choose. Using "
                "an existing name replaces the account behind it."
            )
            return

        self._summary.setText("No accounts yet. Add the first one below.")
        self._summary.setStyleSheet(
            f"font-size: {theme.CAPTION}px; color: {t.caution}; font-weight: 600;"
        )
        self._summary.setVisible(True)
        self._intro.setVisible(True)
        self._signin_step.set_numbered(True)
        self._register_step.set_numbered(True)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._sign_in_button.setEnabled(not busy)
        self._alias_button.setEnabled(not busy and self._alias_account.count() > 0)
        self._sync_register_button()

    def clear_alias(self) -> None:
        """Empty the name fields after a successful registration."""
        self._alias_input.clear()
        self._rename_input.clear()

    def set_result(self, message: str, ok: bool = True) -> None:
        color = "#16a34a" if ok else "#dc2626"
        self._result.setStyleSheet(f"font-size: 12px; color: {color}; font-weight: 600;")
        self._result.setText(message)
