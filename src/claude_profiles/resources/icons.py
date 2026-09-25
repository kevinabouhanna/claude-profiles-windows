"""The tray icon, drawn at runtime.

Generating it avoids shipping binary assets and lets the icon take on the
active profile's colour and glyph. The shape follows Windows tray convention:
a simple, high-contrast mark that stays legible at 16px.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPixmap

from . import fluent_icons

NEUTRAL = "#8A8A8A"


def _draw_tile(size: int, color: str, icon_name: str | None) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    inset = max(1, round(size * 0.06))
    rect = QRect(inset, inset, size - 2 * inset, size - 2 * inset)

    path = QPainterPath()
    radius = size * 0.24
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, QBrush(QColor(color)))

    if icon_name:
        fluent_icons.draw_glyph(
            painter, rect, icon_name, "#FFFFFF", size=int(size * 0.52)
        )
    painter.end()
    return pixmap


def profile_icon(color: str, name: str, size: int = 64) -> QIcon:
    """A rounded tile carrying the profile's Fluent glyph."""
    icon_name = "work" if name.lower().startswith("w") else "personal"
    icon = QIcon()
    for dimension in (16, 20, 24, 32, 48, size):
        icon.addPixmap(_draw_tile(dimension, color, icon_name))
    return icon


def tray_icon(color: str | None = None, name: str | None = None) -> QIcon:
    """Tray icon tinted with the active profile's colour."""
    icon_name = (
        ("work" if name.lower().startswith("w") else "personal") if name else "people"
    )
    icon = QIcon()
    for dimension in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(_draw_tile(dimension, color or NEUTRAL, icon_name))
    return icon


def dot_pixmap(color: str, size: int = 10) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QBrush(QColor(color)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return pixmap


def save_app_icon(path: Path, color: str = "#0078D4") -> Path:
    """Write a multi-resolution .ico for packaging."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _draw_tile(256, color, "people").save(str(path), "ICO")
    return path


def icon_size() -> QSize:
    return QSize(16, 16)
