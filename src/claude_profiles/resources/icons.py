"""Icons drawn at runtime with QPainter.

Generating them avoids shipping binary assets and lets the tray icon take on the
active profile's colour. A PyInstaller build can still render an ``.ico`` from
:func:`save_app_icon`.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

NEUTRAL = "#64748b"


def _draw_badge(size: int, color: str, letter: str, *, ring: bool = False) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    inset = max(1, size // 16)
    rect = QRect(inset, inset, size - 2 * inset, size - 2 * inset)

    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.28, size * 0.28)
    painter.fillPath(path, QBrush(QColor(color)))

    if ring:
        pen = QPen(QColor("#ffffff"))
        pen.setWidth(max(1, size // 16))
        painter.setPen(pen)
        painter.drawPath(path)

    font = QFont("Segoe UI", int(size * 0.5), QFont.Weight.DemiBold)
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return pixmap


def profile_icon(color: str, name: str, size: int = 64) -> QIcon:
    """A rounded square badge carrying the profile's initial."""
    letter = (name[:1] or "?").upper()
    icon = QIcon()
    for dimension in (16, 24, 32, 48, size):
        icon.addPixmap(_draw_badge(dimension, color, letter))
    return icon


def tray_icon(color: str | None = None, letter: str = "C") -> QIcon:
    """Tray icon tinted with the active profile's colour."""
    icon = QIcon()
    for dimension in (16, 20, 24, 32, 48, 64):
        icon.addPixmap(_draw_badge(dimension, color or NEUTRAL, letter, ring=False))
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


def save_app_icon(path: Path, color: str = "#3b82f6", letter: str = "C") -> Path:
    """Write a multi-resolution .ico for packaging."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _draw_badge(256, color, letter).save(str(path), "ICO")
    return path


def icon_size() -> QSize:
    return QSize(16, 16)
