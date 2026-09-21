"""The frameless dashboard that opens on a left-click of the tray icon."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import ProfileState
from .profile_card import ProfileCard
from .theme import hairline, muted_label_css


class HintBanner(QFrame):
    """A dismissable inline note - never a modal, so it cannot block the user."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hintBanner")
        self.setStyleSheet(
            "#hintBanner { background-color: rgba(59, 130, 246, 0.12);"
            "border: 1px solid rgba(59, 130, 246, 0.35); border-radius: 8px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
        layout.setSpacing(8)

        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._label, 1)

        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedSize(20, 20)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.hide)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_message(self, text: str) -> None:
        self._label.setText(text)
        self.show()


class CompactPopup(QWidget):
    """Both profiles at a glance, with one-click actions."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    refreshRequested = Signal()
    dashboardRequested = Signal()
    settingsRequested = Signal()
    quitRequested = Signal()

    def __init__(self, states: tuple[ProfileState, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("compactPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFixedWidth(430)
        self.setStyleSheet(
            "#compactPopup { background-color: palette(window); border: 1px solid "
            + hairline(self)
            + "; border-radius: 12px; }"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # -- header
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Claude Profiles")
        title.setStyleSheet("font-size: 13px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch(1)

        self._refresh_button = QPushButton("⟳")
        self._refresh_button.setFlat(True)
        self._refresh_button.setFixedSize(24, 24)
        self._refresh_button.setToolTip("Refresh now")
        self._refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        header.addWidget(self._refresh_button)

        settings_button = QPushButton("⚙")
        settings_button.setFlat(True)
        settings_button.setFixedSize(24, 24)
        settings_button.setToolTip("Settings")
        settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_button.clicked.connect(self.settingsRequested.emit)
        header.addWidget(settings_button)
        layout.addLayout(header)

        self.banner = HintBanner(self)
        layout.addWidget(self.banner)

        # -- cards
        self._cards: dict[str, ProfileCard] = {}
        for state in states:
            card = ProfileCard(state, compact=True, parent=self)
            card.switchRequested.connect(self.switchRequested.emit)
            card.launchRequested.connect(self.launchRequested.emit)
            card.reloginRequested.connect(self.reloginRequested.emit)
            layout.addWidget(card)
            self._cards[state.profile.key] = card

        # -- footer
        self._status_label = QLabel("Starting…")
        self._status_label.setStyleSheet(muted_label_css(self))
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        footer = QHBoxLayout()
        footer.setSpacing(6)
        for text, signal, tooltip in (
            ("Refresh now", self.refreshRequested, "Poll claude-swap immediately"),
            ("Full dashboard", self.dashboardRequested, "Open the full window"),
            ("Settings", self.settingsRequested, "Preferences and privacy"),
        ):
            button = QPushButton(text)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip(tooltip)
            button.clicked.connect(signal.emit)
            footer.addWidget(button)
        footer.addStretch(1)
        quit_button = QPushButton("Quit")
        quit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_button.clicked.connect(self.quitRequested.emit)
        footer.addWidget(quit_button)
        layout.addLayout(footer)

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

    def set_status(self, text: str) -> None:
        self._status_label.setText(text)

    def show_hint(self, text: str) -> None:
        self.banner.show_message(text)
        self.adjustSize()

    # -- placement ----------------------------------------------------------

    def show_near(self, anchor_point) -> None:
        """Place the popup next to the tray icon, kept inside the screen."""
        self.adjustSize()
        screen = QGuiApplication.screenAt(anchor_point) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()

        x = anchor_point.x() - self.width() // 2
        y = anchor_point.y() - self.height() - 12
        if y < available.top():
            y = anchor_point.y() + 12
        x = max(available.left() + 8, min(x, available.right() - self.width() - 8))
        y = max(available.top() + 8, min(y, available.bottom() - self.height() - 8))

        self.move(int(x), int(y))
        self.show()
        self.raise_()
        self.activateWindow()

    def event(self, event: QEvent) -> bool:
        # A Popup closes on outside clicks; hide rather than destroy so state
        # and signal connections survive until the next open.
        if event.type() == QEvent.Type.Close:
            self.hide()
            event.ignore()
            return True
        return super().event(event)
