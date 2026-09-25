"""The per-profile setup wizard.

Connecting an account is two jobs, and only one of them belongs to this app:

1. **Signing in** is Claude Code's. ``claude auth login`` opens a browser for
   the OAuth flow, so the wizard launches that in a terminal and stays out of
   the way. No credential ever passes through Claude Profiles.
2. **Recording** the account that resulted is a single ``cswap add --alias``
   call, which the wizard runs once the user confirms.

Between the two it polls ``cswap status`` so it can notice the sign-in
finishing on its own. Without that the user would have to come back and press a
refresh button to tell the app something it could see for itself.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import ActiveStatus, Profile
from ..resources import fluent_icons
from . import theme
from .status_badge import ProfileAvatar, StatusBadge

POLL_INTERVAL_MS = 2500
# Stop polling after this long so a dialog left open does not keep spawning
# subprocesses for the rest of the session.
POLL_TIMEOUT_MS = 6 * 60 * 1000


class Step(QFrame):
    """One numbered step with a state line underneath."""

    def __init__(self, number: int, title: str, detail: str) -> None:
        super().__init__()
        t = theme.tokens()
        self.setObjectName("step")
        self.setStyleSheet(theme.card_css("step"))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        self._number = QLabel(str(number))
        self._number.setFixedSize(22, 22)
        self._number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._number.setStyleSheet(
            f"background-color: {t.control}; color: {t.text_secondary};"
            f"border-radius: 11px; font-size: {theme.CAPTION}px; font-weight: 700;"
        )
        layout.addWidget(self._number, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)

        self.title = QLabel(title)
        self.title.setStyleSheet(theme.text_css(theme.BODY, "primary", 600))
        body.addWidget(self.title)

        self.detail = QLabel(detail)
        self.detail.setWordWrap(True)
        self.detail.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        body.addWidget(self.detail)

        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        body.addLayout(self.content)

        layout.addLayout(body, 1)

    def set_done(self, done: bool) -> None:
        t = theme.tokens()
        if done:
            self._number.setText("")
            self._number.setPixmap(fluent_icons.pixmap("success", t.success, 18))
            self._number.setStyleSheet(
                f"background-color: {t.status_tint('success')}; border-radius: 11px;"
            )
        else:
            self._number.setStyleSheet(
                f"background-color: {t.control}; color: {t.text_secondary};"
                f"border-radius: 11px; font-size: {theme.CAPTION}px; font-weight: 700;"
            )


class SetupDialog(QDialog):
    """Guides one profile from "not set up" to registered."""

    signInRequested = Signal()
    registerRequested = Signal(str)  # profile key
    # Emitted from the poll thread. A signal - not QTimer.singleShot - because
    # singleShot called off the GUI thread creates its timer in the *calling*
    # thread, which has no event loop, so the callback never runs. Qt queues
    # signals to the receiver's thread instead.
    _statusReady = Signal(object)

    @property
    def profile(self) -> Profile:
        return self._profile

    def __init__(
        self,
        profile: Profile,
        status_reader: Callable[[], ActiveStatus | None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile = profile
        self._status_reader = status_reader
        self._status: ActiveStatus | None = None
        self._polling = False
        self._poll_inflight = False
        self._baseline_email: str | None = None

        self.setWindowTitle(f"Set up {profile.name}")
        self.setMinimumWidth(520)
        self.setStyleSheet(f"background-color: {theme.tokens().background};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_signin_step())
        layout.addWidget(self._build_account_step())
        layout.addWidget(self._build_register_step())

        self._result = QLabel("")
        self._result.setWordWrap(True)
        self._result.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        layout.addWidget(self._result)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._close_button = QPushButton("Close")
        self._close_button.setStyleSheet(theme.standard_button_css())
        self._close_button.clicked.connect(self.accept)
        footer.addWidget(self._close_button)
        layout.addLayout(footer)

        self._statusReady.connect(self._apply_status)

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll)

        self._stop_timer = QTimer(self)
        self._stop_timer.setSingleShot(True)
        self._stop_timer.setInterval(POLL_TIMEOUT_MS)
        self._stop_timer.timeout.connect(self._stop_polling)

    # -- construction -------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(
            ProfileAvatar(
                self._profile.color,
                "work" if self._profile.key == "work" else "personal",
                size=40,
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(f"Set up {self._profile.name}")
        title.setStyleSheet(theme.text_css(theme.SUBTITLE, "primary", 600))
        text.addWidget(title)
        subtitle = QLabel("Connect a Claude account to this profile.")
        subtitle.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        text.addWidget(subtitle)
        row.addLayout(text, 1)
        return row

    def _build_signin_step(self) -> QWidget:
        self._signin_step = Step(
            1,
            "Sign in to Claude Code",
            "Opens a terminal and your browser. Sign in as the account you "
            f"want to use for {self._profile.name}. You can skip this if the "
            "right account is already signed in below.",
        )
        button = QPushButton("  Sign in to Claude Code")
        button.setIcon(fluent_icons.icon("open_window", theme.tokens().text_on_accent, 16))
        button.setStyleSheet(theme.accent_button_css(self._profile.color))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(self.signInRequested.emit)
        self._signin_button = button
        self._signin_step.content.addWidget(button)

        self._waiting_label = QLabel("")
        self._waiting_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        self._waiting_label.setVisible(False)
        self._signin_step.content.addWidget(self._waiting_label)
        return self._signin_step

    def _build_account_step(self) -> QWidget:
        self._account_step = Step(
            2,
            "Signed in as",
            "Claude Profiles stores whichever account Claude Code is signed in "
            "as right now, so check this is the one you want.",
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        self._email_label = QLabel("Checking…")
        self._email_label.setStyleSheet(theme.text_css(theme.BODY, "primary", 600))
        self._email_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row.addWidget(self._email_label)
        self._email_badge = StatusBadge("", "muted")
        row.addWidget(self._email_badge)
        row.addStretch(1)
        self._account_step.content.addLayout(row)
        return self._account_step

    def _build_register_step(self) -> QWidget:
        self._register_step = Step(
            3,
            f"Save as {self._profile.name}",
            "Records the account with claude-swap under this profile's alias.",
        )
        button = QPushButton(f"  Save this account as {self._profile.name}")
        button.setIcon(fluent_icons.icon("add_account", theme.tokens().text_on_accent, 16))
        button.setStyleSheet(theme.accent_button_css(self._profile.color))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setEnabled(False)
        button.clicked.connect(lambda: self.registerRequested.emit(self._profile.key))
        self._register_button = button
        self._register_step.content.addWidget(button)
        return self._register_step

    # -- polling ------------------------------------------------------------

    def start(self) -> None:
        """Read the current login once, then watch for it changing."""
        self._poll()

    def begin_waiting(self) -> None:
        """Called after the sign-in terminal opens."""
        self._baseline_email = self._status.email if self._status else None
        self._waiting_label.setText(
            "Waiting for sign-in to finish… this updates on its own."
        )
        self._waiting_label.setVisible(True)
        self._signin_button.setText("  Reopen sign-in terminal")
        self._polling = True
        self._poll_timer.start()
        self._stop_timer.start()

    def _stop_polling(self) -> None:
        self._polling = False
        self._poll_timer.stop()
        if self._waiting_label.isVisible():
            self._waiting_label.setText(
                "Stopped watching for a sign-in. Press Re-check if you have "
                "since signed in."
            )
            self._signin_button.setText("  Re-check")

    def _poll(self) -> None:
        if self._poll_inflight:
            return
        self._poll_inflight = True
        threading.Thread(target=self._poll_worker, name="setup-poll", daemon=True).start()

    def _poll_worker(self) -> None:
        try:
            status = self._status_reader()
        except Exception:  # noqa: BLE001 - a failed probe must not kill the dialog
            status = None
        self._statusReady.emit(status)

    def _apply_status(self, status: ActiveStatus | None) -> None:
        self._poll_inflight = False
        self.update_status(status)

    # -- state --------------------------------------------------------------

    def update_status(self, status: ActiveStatus | None) -> None:
        self._status = status
        if status is None or not status.email:
            self._email_label.setText("Not signed in")
            self._email_badge.apply("Unknown", "warn")
            self._register_button.setEnabled(False)
            self._account_step.set_done(False)
            return

        self._email_label.setText(status.email)
        changed = (
            self._baseline_email is not None and status.email != self._baseline_email
        )
        if changed and self._polling:
            # The sign-in landed; stop spawning probes.
            self._stop_polling()
            self._waiting_label.setText("Signed in. Save it below to finish.")
            self._waiting_label.setVisible(True)
            self._signin_step.set_done(True)

        if status.managed:
            self._email_badge.apply("Already in claude-swap", "ok")
        else:
            self._email_badge.apply("New account", "accent")

        self._account_step.set_done(True)
        self._register_button.setEnabled(True)

    def set_busy(self, busy: bool) -> None:
        self._register_button.setEnabled(bool(self._status and not busy))
        self._signin_button.setEnabled(not busy)
        self._register_button.setText(
            "  Saving…" if busy else f"  Save this account as {self._profile.name}"
        )

    def set_result(self, message: str, ok: bool) -> None:
        t = theme.tokens()
        color = t.success if ok else t.critical
        self._result.setStyleSheet(
            f"font-size: {theme.CAPTION}px; color: {color}; font-weight: 600;"
        )
        self._result.setText(message)
        if ok:
            self._register_step.set_done(True)
            self._register_button.setEnabled(False)
            self._close_button.setText("Done")
            self._close_button.setStyleSheet(
                theme.accent_button_css(self._profile.color)
            )
            self._stop_polling()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._stop_polling()
        self._stop_timer.stop()
        super().closeEvent(event)
