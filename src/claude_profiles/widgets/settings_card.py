"""The building blocks of a Windows 11 settings page.

A page is a title, optional sections, and a stack of cards. Each card is one
decision: an icon, a title saying what it is, a line saying what it does, and
the control on the right. That structure is what makes Windows Settings easy
to scan, and it is what the old page lacked - checkboxes, spin boxes and
free-floating notes in group boxes of differing heights.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..resources import fluent_icons
from . import theme
from .buttons import icon_button

CARD_SPACING = 4  # WinUI stacks settings cards 4px apart
SECTION_GAP = 24


class SettingsCard(QFrame):
    """One setting: icon, title, description, and a control on the right."""

    def __init__(
        self,
        glyph: str,
        title: str,
        description: str = "",
        control: QWidget | None = None,
        *,
        icon: QPixmap | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self._glyph = glyph
        self._custom_icon = icon
        self._status_tone = "tertiary"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(68)
        self.setStyleSheet(theme.card_css("settingsCard"))

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(16)

        self._icon = QLabel()
        self._icon.setFixedSize(20, 20)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(title, self)
        self._title.setWordWrap(True)
        text.addWidget(self._title)
        # Parented at birth: ``text`` is not attached to the card until below,
        # so a parentless label shown here would open as a window of its own
        # for an instant - one flash per card when the dashboard first opens.
        self._description = QLabel(description, self)
        self._description.setWordWrap(True)
        self._description.setVisible(bool(description))
        text.addWidget(self._description)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setVisible(False)
        text.addWidget(self._status)
        row.addLayout(text, 1)

        self._control = control
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
            control.setAccessibleName(title)
            control.setAccessibleDescription(description)

        self._apply_colors()

    # -- content ------------------------------------------------------------

    @property
    def control(self) -> QWidget | None:
        return self._control

    def title(self) -> str:
        return self._title.text()

    def description(self) -> str:
        return self._description.text()

    def set_description(self, text: str) -> None:
        self._description.setText(text)
        self._description.setVisible(bool(text))

    def set_status(self, text: str, tone: str = "tertiary") -> None:
        """A second line for outcomes, e.g. a shortcut another app owns."""
        self._status_tone = tone
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self._apply_colors()

    def status(self) -> str:
        return self._status.text()

    def set_available(self, available: bool) -> None:
        """Dim the card and disable its control, e.g. thresholds with alerts off."""
        if self._control is not None:
            self._control.setEnabled(available)
        self.setProperty("available", available)
        self._apply_colors()

    def _apply_colors(self) -> None:
        t = theme.tokens()
        available = self.property("available") is not False
        self._title.setStyleSheet(
            theme.text_css(theme.BODY, "primary" if available else "disabled")
        )
        self._description.setStyleSheet(
            theme.text_css(theme.CAPTION, "secondary" if available else "disabled")
        )
        status_color = {
            "critical": t.critical,
            "caution": t.caution,
            "success": t.success,
        }.get(self._status_tone, t.text_tertiary)
        self._status.setStyleSheet(
            f"font-size: {theme.CAPTION}px; color: {status_color};"
        )
        if self._custom_icon is not None:
            self._icon.setPixmap(self._custom_icon)
        else:
            colour = t.text if available else t.text_disabled
            self._icon.setPixmap(fluent_icons.pixmap(self._glyph, colour, 16))


class BulletCard(QFrame):
    """A card holding a short list of statements, each with a leading glyph."""

    def __init__(self, glyph: str, tone: str, items: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("bulletCard")
        self.setStyleSheet(theme.card_css("bulletCard"))
        t = theme.tokens()
        colour = {"success": t.success, "critical": t.critical}.get(tone, t.text_secondary)

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(10)
        self._labels: list[QLabel] = []
        for item in items:
            row = QHBoxLayout()
            row.setSpacing(12)
            icon = QLabel()
            icon.setFixedSize(16, 18)
            icon.setPixmap(fluent_icons.pixmap(glyph, colour, 14))
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
            label = QLabel(item)
            label.setWordWrap(True)
            label.setStyleSheet(theme.text_css(theme.BODY, "secondary"))
            row.addWidget(label, 1)
            column.addLayout(row)
            self._labels.append(label)

    def texts(self) -> list[str]:
        return [label.text() for label in self._labels]


class SectionHeader(QLabel):
    """A group title above a run of cards."""

    def __init__(self, text: str, *, first: bool = False, parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setStyleSheet(
            theme.text_css(theme.BODY, "primary", 600)
            + f" padding-top: {0 if first else SECTION_GAP - CARD_SPACING}px;"
            f" padding-bottom: {8 - CARD_SPACING}px;"
        )


class InfoBar(QFrame):
    """A WinUI InfoBar: an inline, dismissable message with a severity."""

    closed = Signal()

    _SEVERITY = {
        "info": ("info", "accent"),
        "success": ("success", "success"),
        "caution": ("warning", "caution"),
        "critical": ("error", "critical"),
    }

    def __init__(self, severity: str = "info", *, closable: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("infoBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 8, 10)
        row.setSpacing(12)
        self._icon = QLabel()
        self._icon.setFixedSize(16, 18)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        self._message = QLabel("")
        self._message.setWordWrap(True)
        self._message.setStyleSheet(theme.text_css(theme.BODY, "primary"))
        row.addWidget(self._message, 1)
        if closable:
            close = icon_button("close", "Dismiss", size=26)
            close.clicked.connect(self._dismiss)
            row.addWidget(close, 0, Qt.AlignmentFlag.AlignTop)
        self.set_severity(severity)
        self.hide()

    def set_severity(self, severity: str) -> None:
        t = theme.tokens()
        glyph, token = self._SEVERITY.get(severity, self._SEVERITY["info"])
        colour = getattr(t, token)
        tint = t.accent_tint(0.14) if token == "accent" else t.status_tint(token, 0.14)
        edge = t.accent_tint(0.35) if token == "accent" else t.status_tint(token, 0.35)
        self._icon.setPixmap(fluent_icons.pixmap(glyph, colour, 16))
        self.setStyleSheet(
            f"#infoBar {{ background-color: {tint}; border: 1px solid {edge};"
            f" border-radius: {theme.RADIUS_CONTROL}px; }}"
        )

    def show_message(self, text: str, severity: str | None = None) -> None:
        if severity:
            self.set_severity(severity)
        self._message.setText(text)
        self.setVisible(bool(text))

    def message(self) -> str:
        return self._message.text()

    def _dismiss(self) -> None:
        self.hide()
        self.closed.emit()


class Page(QWidget):
    """A navigation destination: a title, optional description, then content.

    ``scroll`` pages hold a stack of cards and scroll as a whole. Pages whose
    main content scrolls on its own - the activity list - set it False so the
    list, not the page, takes the remaining height.
    """

    def __init__(self, title: str, description: str = "", *, scroll: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scroll = scroll

        content = QWidget()
        content.setObjectName("pageContent")
        column = QVBoxLayout(content)
        column.setContentsMargins(36, 24, 36, 32)
        column.setSpacing(0)

        header = QHBoxLayout()
        header.setSpacing(8)
        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"font-size: {theme.TITLE}px; font-weight: 600; color: {theme.tokens().text};"
        )
        self._title.setFont(theme.font(theme.TITLE, 600))
        header.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.header_actions = header
        column.addLayout(header)

        if description:
            column.addSpacing(4)
            text = QLabel(description)
            text.setWordWrap(True)
            text.setStyleSheet(theme.text_css(theme.BODY, "secondary"))
            column.addWidget(text)
        column.addSpacing(20)

        self.body = QVBoxLayout()
        self.body.setSpacing(CARD_SPACING)
        self.body.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self.body, 1)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setStyleSheet(
                "QScrollArea { background: transparent; border: none; }"
                "#pageContent { background: transparent; }"
            )
            area.viewport().setAutoFillBackground(False)
            area.setWidget(content)
            outer.addWidget(area)
        else:
            content.setStyleSheet("#pageContent { background: transparent; }")
            outer.addWidget(content)

    def title(self) -> str:
        return self._title.text()

    def add_header_action(self, widget: QWidget) -> None:
        self.header_actions.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)

    def add_section(self, text: str) -> SectionHeader:
        header = SectionHeader(text, first=self.body.count() == 0)
        self.body.addWidget(header)
        return header

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self.body.addWidget(widget, stretch)
        return widget

    def finish(self) -> None:
        """Pin the content to the top of a scrolling page."""
        if self._scroll:
            self.body.addStretch(1)
