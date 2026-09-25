"""The frameless flyout that opens on a left-click of the tray icon.

Modelled on a Windows 11 tray flyout: a rounded surface, a title row with
subtle icon buttons, content cards, and a command row along the bottom.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..models import ProfileState
from ..resources import fluent_icons
from . import theme
from .profile_card import ProfileCard

POPUP_WIDTH = 440


def icon_button(name: str, tooltip: str, size: int = 32) -> QPushButton:
    """A Fluent subtle button carrying a system glyph and no text."""
    t = theme.tokens()
    button = QPushButton()
    button.setIcon(fluent_icons.icon(name, t.text_secondary, 16))
    button.setIconSize(fluent_icons.icon_size(16))
    button.setFixedSize(size, size)
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(theme.subtle_button_css())
    return button


def text_button(name: str, text: str, *, accent: bool = False) -> QPushButton:
    t = theme.tokens()
    button = QPushButton(f"  {text}")
    color = t.text_on_accent if accent else t.text_secondary
    button.setIcon(fluent_icons.icon(name, color, 16))
    button.setIconSize(fluent_icons.icon_size(16))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        theme.accent_button_css() if accent else theme.standard_button_css()
    )
    return button


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
    """Both profiles at a glance, with one-click actions."""

    switchRequested = Signal(str)
    launchRequested = Signal(str)
    reloginRequested = Signal(str)
    setupRequested = Signal(str)
    refreshRequested = Signal()
    dashboardRequested = Signal()
    settingsRequested = Signal()
    quitRequested = Signal()

    def __init__(self, states: tuple[ProfileState, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("compactPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(POPUP_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())
        self.banner = HintBanner(self)
        layout.addWidget(self.banner)

        self._cards: dict[str, ProfileCard] = {}
        for state in states:
            card = ProfileCard(state, compact=True, parent=self)
            card.switchRequested.connect(self.switchRequested.emit)
            card.launchRequested.connect(self.launchRequested.emit)
            card.reloginRequested.connect(self.reloginRequested.emit)
            card.setupRequested.connect(self.setupRequested.emit)
            layout.addWidget(card)
            self._cards[state.profile.key] = card

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

        self._refresh_button = icon_button("refresh", "Refresh now")
        self._refresh_button.clicked.connect(self.refreshRequested.emit)
        header.addWidget(self._refresh_button)

        settings_button = icon_button("settings", "Settings")
        settings_button.clicked.connect(self.settingsRequested.emit)
        header.addWidget(settings_button)

        close_button = icon_button("close", "Close")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button)
        return header

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

    def update_states(self, states: tuple[ProfileState, ...]) -> None:
        for state in states:
            card = self._cards.get(state.profile.key)
            if card is not None:
                card.set_state(state)

    def set_busy(self, busy: bool) -> None:
        for card in self._cards.values():
            card.set_busy(busy)
        self._refresh_button.setEnabled(not busy)

    def set_status(self, text: str, level: str = "info") -> None:
        self._status_label.setText(text)
        self._set_status_icon(level)

    def show_hint(self, text: str) -> None:
        self.banner.show_message(text)
        self.adjustSize()

    # -- placement ----------------------------------------------------------

    def show_near(self, anchor_point) -> None:
        """Place the flyout next to the tray icon, kept inside the screen."""
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
