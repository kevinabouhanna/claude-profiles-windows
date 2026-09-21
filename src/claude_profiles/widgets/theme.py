"""Palette-derived colours for stylesheets.

Qt's ``palette(mid)`` role is a border/shadow colour, not a readable-text
colour: on a dark theme it lands close to the background and secondary text
disappears. Deriving muted tones from the *text* colour instead keeps contrast
correct in both light and dark themes.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

MUTED_ALPHA = 0.66
FAINT_ALPHA = 0.45
HAIRLINE_ALPHA = 0.22


def _rgba(widget: QWidget, alpha: float, role: str = "text") -> str:
    palette = widget.palette()
    color = palette.text().color() if role == "text" else palette.window().color()
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {alpha})"


def muted(widget: QWidget) -> str:
    """Secondary text: readable but clearly subordinate."""
    return _rgba(widget, MUTED_ALPHA)


def faint(widget: QWidget) -> str:
    """Tertiary text and disabled labels."""
    return _rgba(widget, FAINT_ALPHA)


def hairline(widget: QWidget) -> str:
    """Borders and separators."""
    return _rgba(widget, HAIRLINE_ALPHA)


def muted_label_css(widget: QWidget, size: int = 11) -> str:
    return f"font-size: {size}px; color: {muted(widget)};"
