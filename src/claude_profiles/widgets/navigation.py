"""A left-hand navigation pane in the style of WinUI's NavigationView.

Items are grouped the way Windows apps group them: the things you come to the
app to do at the top, and the things about the app itself - privacy and
settings - pinned to the bottom. The selected item gets a subtle fill and the
short accent pill on its leading edge that marks selection throughout
Windows 11.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QSizePolicy, QVBoxLayout, QWidget

from ..resources import fluent_icons
from . import theme

PANE_WIDTH = 220
ITEM_HEIGHT = 40

_KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
    Qt.FocusReason.OtherFocusReason,
)


class NavItem(QAbstractButton):
    """One destination: glyph, label, selection pill."""

    def __init__(self, glyph: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glyph = glyph
        self.setText(text)
        self.setAccessibleName(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(ITEM_HEIGHT)
        self._hover = False
        self._keyboard_focus = False

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(PANE_WIDTH - 8, ITEM_HEIGHT)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._keyboard_focus = event.reason() in _KEYBOARD_REASONS
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._keyboard_focus = False
        self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._keyboard_focus = False
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt naming
        pane = self.parent()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        elif event.key() == Qt.Key.Key_Down and isinstance(pane, NavigationPane):
            pane.focus_neighbour(self, +1)
        elif event.key() == Qt.Key.Key_Up and isinstance(pane, NavigationPane):
            pane.focus_neighbour(self, -1)
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        t = theme.tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        area = QRectF(self.rect()).adjusted(4, 2, -4, -2)
        if self.isChecked() or self._hover or self.isDown():
            if self.isDown():
                fill = QColor(t.control_pressed)
            elif self.isChecked():
                fill = QColor(t.control_hover) if self._hover else QColor(t.control)
            else:
                fill = QColor(t.subtle_hover)
            path = QPainterPath()
            path.addRoundedRect(area, theme.RADIUS_CONTROL, theme.RADIUS_CONTROL)
            painter.fillPath(path, fill)

        if self.isChecked():
            pill = QRectF(area.left(), area.center().y() - 8, 3, 16)
            path = QPainterPath()
            path.addRoundedRect(pill, 1.5, 1.5)
            painter.fillPath(path, QColor(t.accent))

        colour = t.text if self.isEnabled() else t.text_disabled
        icon_box = QRectF(area.left() + 12, area.top(), 20, area.height())
        painter.drawPixmap(
            int(icon_box.left() + 2),
            int(icon_box.center().y() - 8),
            fluent_icons.pixmap(self._glyph, colour, 16),
        )

        painter.setFont(theme.font(theme.BODY, 600 if self.isChecked() else 400))
        painter.setPen(QColor(colour))
        text_box = QRectF(area.left() + 44, area.top(), area.width() - 52, area.height())
        text = painter.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, int(text_box.width())
        )
        painter.drawText(text_box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)

        if self._keyboard_focus:
            painter.setPen(QPen(QColor(t.text), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            ring = area.adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(ring, theme.RADIUS_CONTROL, theme.RADIUS_CONTROL)
        painter.end()


class NavigationPane(QWidget):
    """Top items, a flexible gap, then footer items."""

    currentChanged = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(PANE_WIDTH)
        self._items: list[NavItem] = []
        self._current = -1
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 8, 4, 8)
        layout.setSpacing(2)
        self._top = QVBoxLayout()
        self._top.setSpacing(2)
        self._footer = QVBoxLayout()
        self._footer.setSpacing(2)
        layout.addLayout(self._top)
        layout.addStretch(1)
        layout.addLayout(self._footer)

    def add_item(self, glyph: str, text: str, *, footer: bool = False) -> int:
        index = len(self._items)
        item = NavItem(glyph, text, self)
        self._group.addButton(item, index)
        item.clicked.connect(lambda _checked=False, i=index: self.set_current(i))
        (self._footer if footer else self._top).addWidget(item)
        self._items.append(item)
        return index

    @property
    def current_index(self) -> int:
        return self._current

    def item(self, index: int) -> NavItem:
        return self._items[index]

    def count(self) -> int:
        return len(self._items)

    def set_current(self, index: int) -> None:
        if not 0 <= index < len(self._items):
            return
        self._items[index].setChecked(True)
        if index == self._current:
            return
        self._current = index
        self.currentChanged.emit(index)

    def focus_neighbour(self, item: NavItem, delta: int) -> None:
        index = self._items.index(item)
        target = (index + delta) % len(self._items)
        self._items[target].setFocus(Qt.FocusReason.TabFocusReason)
