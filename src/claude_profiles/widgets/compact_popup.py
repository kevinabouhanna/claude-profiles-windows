"""The frameless flyout that opens on a left-click of the tray icon.

Modelled on a Windows 11 tray flyout: a rounded surface, a title row with
subtle icon buttons, content cards, and a command row along the bottom.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..models import ProfileState
from ..resources import fluent_icons
from . import theme
from .buttons import icon_button, text_button
from .profile_card import ProfileCard

POPUP_WIDTH = 440
# Header, status line and footer, plus the outer margins: everything in the
# flyout that is not a card. Used to cap the card area to the screen.
CHROME_HEIGHT = 150
CARD_SPACING = 10
# Clicking the tray icon while the flyout is open closes it twice over: the
# press lands outside a Qt popup, which hides it at once, and then the tray
# reports the click, which would open it straight back up. A tray click this
# soon after a hide is taken to be that same click.
REOPEN_GUARD_SECONDS = 0.5


class HintBanner(QFrame):
    """A dismissable inline note - never a modal, so it cannot block the user."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        t = theme.tokens()
        self.setObjectName("hintBanner")
        self.setStyleSheet(
            f"#hintBanner {{ background-color: {t.accent_tint(0.12)};"
            f" border: 1px solid {t.accent_tint(0.30)};"
            f" border-radius: {theme.RADIUS_CONTROL}px; }}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 6, 8)
        layout.setSpacing(8)

        self._icon = QLabel()
        self._icon.setPixmap(fluent_icons.pixmap("info", t.accent, 14))
        self._icon.setFixedWidth(16)
        layout.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)

        self._label = QLabel("")
        self._label.setWordWrap(True)
        self._label.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        layout.addWidget(self._label, 1)

        close = icon_button("close", "Dismiss", size=22)
        close.clicked.connect(self.hide)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.hide()

    def show_message(self, text: str) -> None:
        self._label.setText(text)
        self.show()


class CompactPopup(QWidget):
    """Every account at a glance, with one-click actions."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    setupRequested = Signal(str)
    addAccountRequested = Signal()
    dashboardRequested = Signal()
    settingsRequested = Signal()
    quitRequested = Signal()
    visibilityChanged = Signal(bool)

    def __init__(self, states: tuple[ProfileState, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("compactPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(POPUP_WIDTH)
        self._hidden_at: float | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())
        self.banner = HintBanner(self)
        layout.addWidget(self.banner)

        self._cards: dict[str, ProfileCard] = {}
        self._max_cards_height = 560
        layout.addWidget(self._build_cards_area())
        layout.addWidget(self._build_empty_state())
        self.set_profiles(states)

        layout.addLayout(self._build_status_row())
        layout.addLayout(self._build_footer())

    # -- construction -------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(4)

        title = QLabel("Claude Profiles")
        title.setStyleSheet(theme.text_css(theme.BODY, "primary", 600))
        header.addWidget(title)
        header.addStretch(1)

        add_button = icon_button("add_account", "Add an account")
        add_button.clicked.connect(self.addAccountRequested.emit)
        header.addWidget(add_button)

        settings_button = icon_button("settings", "Settings")
        settings_button.clicked.connect(self.settingsRequested.emit)
        header.addWidget(settings_button)

        close_button = icon_button("close", "Close")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button)
        return header

    def _build_cards_area(self) -> QScrollArea:
        """Cards stack and grow; past the screen's height they scroll."""
        host = QWidget()
        host.setObjectName("popupCards")
        host.setStyleSheet("#popupCards { background: transparent; }")
        self._cards_layout = QVBoxLayout(host)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.setSpacing(CARD_SPACING)

        area = QScrollArea()
        area.setWidget(host)
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        area.viewport().setAutoFillBackground(False)
        self._cards_host = host
        self._scroll = area
        return area

    def _build_empty_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("popupEmpty")
        frame.setStyleSheet(theme.card_css("popupEmpty"))
        column = QVBoxLayout(frame)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(10)
        text = QLabel(
            "No accounts yet. Sign in to Claude Code as each account you use and "
            "register it, and its usage appears here."
        )
        text.setWordWrap(True)
        text.setStyleSheet(theme.text_css(theme.CAPTION, "secondary"))
        column.addWidget(text)
        button = text_button("add_account", "Add an account")
        button.clicked.connect(self.addAccountRequested.emit)
        column.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        self._empty = frame
        return frame

    def _build_status_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        self._status_icon = QLabel()
        self._status_icon.setFixedWidth(14)
        row.addWidget(self._status_icon, 0, Qt.AlignmentFlag.AlignTop)
        self._status_label = QLabel("Starting…")
        self._status_label.setStyleSheet(theme.text_css(theme.CAPTION, "tertiary"))
        self._status_label.setWordWrap(True)
        row.addWidget(self._status_label, 1)
        self._set_status_icon("info")
        return row

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)

        dashboard = text_button("open_window", "Dashboard")
        dashboard.clicked.connect(self.dashboardRequested.emit)
        footer.addWidget(dashboard)

        accounts = text_button("people", "Accounts")
        accounts.clicked.connect(lambda: self.setupRequested.emit(""))
        footer.addWidget(accounts)

        footer.addStretch(1)

        quit_button = icon_button("power", "Quit Claude Profiles")
        quit_button.clicked.connect(self.quitRequested.emit)
        footer.addWidget(quit_button)
        return footer

    def _set_status_icon(self, name: str) -> None:
        t = theme.tokens()
        color = {"info": t.text_tertiary, "warning": t.caution, "error": t.critical}.get(
            name, t.text_tertiary
        )
        self._status_icon.setPixmap(fluent_icons.pixmap(name, color, 13))

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # The translucent window attribute means the rounded surface and its
        # border are drawn here rather than by the stylesheet.
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.fillPath(path, QColor(t.background))
        painter.strokePath(path, QColor(t.stroke))
        painter.end()

    # -- updates ------------------------------------------------------------

    def set_profiles(self, states: tuple[ProfileState, ...]) -> None:
        """Rebuild the cards after an account is added, removed or renamed."""
        for card in self._cards.values():
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        for state in states:
            card = ProfileCard(state, compact=True, parent=self._cards_host)
            card.switchRequested.connect(self.switchRequested.emit)
            card.launchRequested.connect(self.launchRequested.emit)
            card.reloginRequested.connect(self.reloginRequested.emit)
            card.setupRequested.connect(self.setupRequested.emit)
            self._cards_layout.addWidget(card)
            self._cards[state.profile.key] = card
        self._scroll.setVisible(bool(states))
        self._empty.setVisible(not states)
        self._fit_cards()

    def _fit_cards(self) -> None:
        """Size the card area to its content, up to what the screen allows."""
        if not self._cards:
            return
        content = self._cards_layout.sizeHint().height()
        self._scroll.setFixedHeight(min(content, self._max_cards_height))
        if self.isVisible():
            self.adjustSize()

    def update_states(self, states: tuple[ProfileState, ...]) -> None:
        for state in states:
            card = self._cards.get(state.profile.key)
            if card is not None:
                card.set_state(state)
        # A card grows when a per-model row appears, so re-fit.
        self._fit_cards()

    def set_busy(self, busy: bool) -> None:
        for card in self._cards.values():
            card.set_busy(busy)

    def set_status(self, text: str, level: str = "info") -> None:
        self._status_label.setText(text)
        self._set_status_icon(level)

    def show_hint(self, text: str) -> None:
        self.banner.show_message(text)
        self.adjustSize()

    # -- placement ----------------------------------------------------------

    def show_near(self, anchor_point) -> None:
        """Place the flyout next to the tray icon, kept inside the screen."""
        screen = QGuiApplication.screenAt(anchor_point) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry()
        self._max_cards_height = max(240, available.height() - CHROME_HEIGHT - 40)
        self._fit_cards()
        self.adjustSize()

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

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.visibilityChanged.emit(True)

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self._hidden_at = time.monotonic()
        self.visibilityChanged.emit(False)

    def just_hidden(self) -> bool:
        """Did the flyout close within the last REOPEN_GUARD_SECONDS?"""
        return (
            self._hidden_at is not None
            and time.monotonic() - self._hidden_at < REOPEN_GUARD_SECONDS
        )

    def event(self, event: QEvent) -> bool:
        # A Popup closes on outside clicks; hide rather than destroy so state
        # and signal connections survive until the next open.
        if event.type() == QEvent.Type.Close:
            self.hide()
            event.ignore()
            return True
        return super().event(event)
