"""Fluent button helpers shared by the flyout and the main window."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPushButton

from ..resources import fluent_icons
from . import theme


def icon_button(name: str, tooltip: str, size: int = 32) -> QPushButton:
    """A Fluent subtle button carrying a system glyph and no text."""
    t = theme.tokens()
    button = QPushButton()
    button.setIcon(fluent_icons.icon(name, t.text_secondary, 16))
    button.setIconSize(fluent_icons.icon_size(16))
    button.setFixedSize(size, size)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(theme.subtle_button_css())
    return button


def text_button(name: str, text: str, *, accent: bool = False) -> QPushButton:
    """A standard (or accent) button with a leading system glyph."""
    t = theme.tokens()
    button = QPushButton(f"  {text}")
    button.setAccessibleName(text)
    color = t.text_on_accent if accent else t.text_secondary
    button.setIcon(fluent_icons.icon(name, color, 16))
    button.setIconSize(fluent_icons.icon_size(16))
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setStyleSheet(
        theme.accent_button_css() if accent else theme.standard_button_css()
    )
    return button
