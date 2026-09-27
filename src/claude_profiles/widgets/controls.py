"""Fluent controls that Qt does not provide: a toggle switch and a stepper.

Settings used checkboxes and spin boxes, which is part of why the page read as
a form rather than as Windows settings. Windows 11 uses a ToggleSwitch for
on/off choices and a compact "- value +" NumberBox for small numeric ones, so
these are drawn to match: sizes, radii and states follow the WinUI templates.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QKeyEvent, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from ..resources import fluent_icons
from . import theme

_KEYBOARD_REASONS = (
    Qt.FocusReason.TabFocusReason,
    Qt.FocusReason.BacktabFocusReason,
    Qt.FocusReason.ShortcutFocusReason,
)


def _blend(a: QColor, b: QColor, amount: float) -> QColor:
    amount = max(0.0, min(1.0, amount))
    return QColor.fromRgbF(
        a.redF() + (b.redF() - a.redF()) * amount,
        a.greenF() + (b.greenF() - a.greenF()) * amount,
        a.blueF() + (b.blueF() - a.blueF()) * amount,
        a.alphaF() + (b.alphaF() - a.alphaF()) * amount,
    )


class ToggleSwitch(QAbstractButton):
    """A WinUI ToggleSwitch: a 40x20 pill with a knob that slides across.

    Off is an outlined track with a grey knob; on is an accent-filled track
    with a white knob. The knob grows on hover, as in WinUI. The focus ring is
    shown only for keyboard focus, so a mouse click does not leave it behind.
    """

    TRACK_WIDTH = 40
    TRACK_HEIGHT = 20

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._position = 0.0  # 0 = off, 1 = on
        self._hover = False
        self._keyboard_focus = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._on_step)
        self.toggled.connect(self._slide)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return QSize(self.TRACK_WIDTH + 8, self.TRACK_HEIGHT + 8)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt naming
        return self.sizeHint()

    # -- state --------------------------------------------------------------

    def _slide(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if not self.isVisible():
            # No point animating something nobody can see; jump straight there.
            self._animation.stop()
            self._position = target
            self.update()
            return
        self._animation.stop()
        self._animation.setStartValue(self._position)
        self._animation.setEndValue(target)
        self._animation.start()

    def _on_step(self, value) -> None:
        self._position = float(value)
        self.update()

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

    # -- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        t = theme.tokens()
        enabled = self.isEnabled()
        active = enabled and (self._hover or self.isDown())

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        left = (self.width() - self.TRACK_WIDTH) / 2
        top = (self.height() - self.TRACK_HEIGHT) / 2
        # Half-pixel inset so the 1px outline lands on whole pixels.
        track = QRectF(left + 0.5, top + 0.5, self.TRACK_WIDTH - 1, self.TRACK_HEIGHT - 1)
        radius = track.height() / 2
        position = self._position

        if position < 1.0:
            painter.setOpacity(1.0 - position)
            painter.setBrush(QColor(t.control_hover) if active else Qt.GlobalColor.transparent)
            painter.setPen(QPen(QColor(t.text_secondary if enabled else t.text_disabled), 1))
            painter.drawRoundedRect(track, radius, radius)

        if position > 0.0:
            painter.setOpacity(position)
            fill = QColor(t.accent if enabled else t.text_disabled)
            if active:
                fill = fill.lighter(110)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            filled = track.adjusted(-0.5, -0.5, 0.5, 0.5)
            painter.drawRoundedRect(filled, radius + 0.5, radius + 0.5)

        painter.setOpacity(1.0)
        diameter = 14.0 if active else 12.0
        travel = track.width() - 2 * (radius)
        centre = QPointF(track.left() + radius + position * travel, track.center().y())
        knob_off = QColor(t.text_secondary if enabled else t.text_disabled)
        knob_on = QColor(t.text_on_accent if enabled else t.card)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_blend(knob_off, knob_on, position))
        painter.drawEllipse(centre, diameter / 2, diameter / 2)

        if self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(t.text), 2))
            ring = track.adjusted(-3, -3, 3, 3)
            painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
        painter.end()


def format_interval(seconds: int) -> str:
    """``30 s``, ``2 min``, ``1 hour``."""
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds / 60:g} min"
    hours = seconds / 3600
    return f"{hours:g} hour" + ("" if hours == 1 else "s")


class Stepper(QFrame):
    """A compact ``- value +`` NumberBox, like the one in Windows Settings.

    Steps either by a fixed increment or through a list of choices. A value
    that is not on the grid (say 83 when stepping by 5, from a hand-edited
    settings file) is shown as it is and snaps to the grid on the next press,
    rather than being silently rewritten on load.
    """

    valueChanged = Signal(int)

    def __init__(
        self,
        value: int,
        *,
        minimum: int = 0,
        maximum: int = 100,
        step: int = 1,
        choices: Sequence[int] | None = None,
        formatter: Callable[[int], str] = str,
        label: str = "value",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("stepper")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(label)
        self._choices = sorted(set(choices)) if choices else None
        self._minimum = self._choices[0] if self._choices else minimum
        self._maximum = self._choices[-1] if self._choices else maximum
        self._step = step
        self._formatter = formatter
        self._value = self._clamp(value)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self._down = self._make_button("minus", f"Decrease {label}")
        self._down.clicked.connect(self.step_down)
        layout.addWidget(self._down)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setMinimumWidth(64)
        layout.addWidget(self._label)

        self._up = self._make_button("add", f"Increase {label}")
        self._up.clicked.connect(self.step_up)
        layout.addWidget(self._up)

        self._restyle()
        self._refresh()

    def _make_button(self, glyph: str, name: str) -> QPushButton:
        button = QPushButton()
        button.setFixedSize(32, 30)
        button.setIconSize(fluent_icons.icon_size(12))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # the stepper itself takes focus
        button.setProperty("glyph", glyph)
        return button

    # -- value --------------------------------------------------------------

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:  # noqa: N802 - Qt naming
        value = self._clamp(int(value))
        if value == self._value:
            return
        self._value = value
        self._refresh()
        self.valueChanged.emit(value)

    def set_range(self, minimum: int, maximum: int) -> None:
        """Narrow the range; used to keep a warning below its critical level."""
        if self._choices is not None:
            return
        self._minimum, self._maximum = minimum, max(minimum, maximum)
        clamped = self._clamp(self._value)
        if clamped != self._value:
            self._value = clamped
            self.valueChanged.emit(clamped)
        self._refresh()

    def _clamp(self, value: int) -> int:
        return max(self._minimum, min(self._maximum, value))

    def _next(self, direction: int) -> int:
        value = self._value
        if self._choices is not None:
            if direction > 0:
                higher = [c for c in self._choices if c > value]
                return higher[0] if higher else value
            lower = [c for c in self._choices if c < value]
            return lower[-1] if lower else value
        step = self._step
        if direction > 0:
            return self._clamp(value + step - (value % step))
        return self._clamp(value - step if value % step == 0 else value - (value % step))

    def step_up(self) -> None:
        self.setValue(self._next(+1))

    def step_down(self) -> None:
        self.setValue(self._next(-1))

    # -- presentation -------------------------------------------------------

    def _refresh(self) -> None:
        self._label.setText(self._formatter(self._value))
        self._up.setEnabled(self.isEnabled() and self._next(+1) != self._value)
        self._down.setEnabled(self.isEnabled() and self._next(-1) != self._value)
        self._label.setAccessibleName(f"{self.accessibleName()}: {self._label.text()}")

    def _restyle(self) -> None:
        t = theme.tokens()
        enabled = self.isEnabled()
        self.setStyleSheet(
            f"#stepper {{ background-color: {t.control}; border: 1px solid {t.stroke};"
            f" border-radius: {theme.RADIUS_CONTROL}px; }}"
            f"#stepper:focus {{ border: 1px solid {t.accent}; }}"
        )
        self._label.setStyleSheet(
            theme.text_css(theme.BODY, "primary" if enabled else "disabled")
        )
        for button in (self._down, self._up):
            button.setStyleSheet(
                theme.subtle_button_css() + "QPushButton { border-radius: 3px; padding: 0; }"
            )
            colour = t.text_secondary if enabled else t.text_disabled
            button.setIcon(fluent_icons.icon(button.property("glyph"), colour, 12))

    def changeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.EnabledChange:
            self._restyle()
            self._refresh()
        super().changeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Right, Qt.Key.Key_Plus, Qt.Key.Key_PageUp):
            self.step_up()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Minus, Qt.Key.Key_PageDown):
            self.step_down()
        else:
            super().keyPressEvent(event)
