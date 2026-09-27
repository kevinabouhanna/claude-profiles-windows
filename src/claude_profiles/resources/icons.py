"""Application and notification-area icons, drawn at runtime.

Two different icons, because Windows treats the two places differently:

**The tray icon** follows the notification-area convention of Windows 11's own
icons (volume, network, battery): a single-colour glyph that is white on a dark
taskbar and near-black on a light one. It is drawn from Segoe Fluent Icons at
the exact pixel size the tray asks for - 16px at 100% scaling - because the
font is hinted for those sizes. The previous icon was a tinted tile with a
glyph shrunk to roughly ten pixels inside it, and antialiasing that into a
16x16 cell is what made it look soft. The active profile is shown as a small
status badge, the way Windows badges its own network and battery icons.

**The app icon** (window, taskbar button, Start menu, shortcuts, the exe) is a
coloured tile, which is what app icons are. It is written to disk as a proper
multi-resolution .ico so Windows never scales one large image down.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPixmap,
)

from . import fluent_icons

APP_COLOR = "#0078D4"  # Windows default accent

# Sizes Windows asks a notification-area icon for, from 100% to 400% scaling.
TRAY_SIZES = (16, 20, 24, 28, 32, 40, 48, 64)
# Sizes an .ico should carry so Explorer, Start and the taskbar never rescale.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

TRAY_GLYPH = "switch_user"


def _glyph_font(pixel_size: int) -> QFont:
    """The system icon font at an exact pixel size.

    Pixel size, not point size: the glyphs are designed on a 16px em square,
    so asking for 16px yields the hand-tuned 16px shape rather than a scaled
    approximation of it.
    """
    font = QFont(fluent_icons.icon_family() or "Segoe UI")
    font.setPixelSize(max(1, pixel_size))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _new_image(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    return image


# -- tray --------------------------------------------------------------------


def render_tray(size: int, badge_color: str | None, dark_taskbar: bool) -> QImage:
    """One tray icon at one size."""
    image = _new_image(size)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    glyph_color = QColor("#FFFFFF") if dark_taskbar else QColor("#1A1A1A")
    char = fluent_icons.glyph(TRAY_GLYPH)
    if char:
        painter.setFont(_glyph_font(size))
        painter.setPen(glyph_color)
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, char)

    if badge_color:
        # Top-right: the glyph's switch arrows occupy the bottom-right corner.
        # Sized to clear the head of the glyph: at 16px a 5px dot plus a 1px
        # knockout ring sits in the empty top-right corner without clipping it.
        diameter = max(5.0, round(size * 0.30))
        # Floor, not round: rounding 1.5 up at 24px made the ring eat the head.
        gap = float(max(1, size // 16))
        centre = QPointF(size - diameter / 2, diameter / 2)

        # Knock a ring out of the glyph so the badge reads as separate from it,
        # as Windows does for its own badged tray icons.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(Qt.GlobalColor.black)
        painter.drawEllipse(centre, diameter / 2 + gap, diameter / 2 + gap)

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        colour = QColor(badge_color)
        if dark_taskbar:
            # Lift the identity colour a touch so it holds up on a dark bar.
            h, s, lightness, a = colour.getHslF()
            colour = QColor.fromHslF(h, s, min(1.0, lightness + 0.08), a)
        painter.setBrush(colour)
        painter.drawEllipse(centre, diameter / 2, diameter / 2)

    painter.end()
    return image


def tray_icon(badge_color: str | None = None, *, dark_taskbar: bool | None = None) -> QIcon:
    """The notification-area icon, with an optional active-profile badge.

    ``dark_taskbar`` defaults to the live Windows setting, so the glyph stays
    visible whichever way the user's taskbar is themed.
    """
    if dark_taskbar is None:
        from ..widgets.theme import taskbar_is_dark

        dark_taskbar = taskbar_is_dark()
    icon = QIcon()
    for size in TRAY_SIZES:
        icon.addPixmap(QPixmap.fromImage(render_tray(size, badge_color, dark_taskbar)))
    return icon


# -- app ---------------------------------------------------------------------


def render_app_tile(size: int, color: str = APP_COLOR) -> QImage:
    """The coloured app tile at one size."""
    image = _new_image(size)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

    base = QColor(color)
    h, s, lightness, a = base.getHslF()
    top = QColor.fromHslF(h, s, min(1.0, lightness + 0.10), a)
    bottom = QColor.fromHslF(h, s, max(0.0, lightness - 0.04), a)

    # Fill the full cell at small sizes, where every pixel of margin is a pixel
    # of glyph lost; leave a little breathing room at large sizes.
    inset = 0.0 if size <= 32 else size * 0.04
    rect = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    radius = rect.width() * 0.22

    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(1.0, bottom)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, QBrush(gradient))

    char = fluent_icons.glyph(TRAY_GLYPH)
    if char:
        glyph_px = round(rect.width() * (0.72 if size <= 24 else 0.62))
        painter.setFont(_glyph_font(glyph_px))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, char)

    painter.end()
    return image


def app_icon(color: str = APP_COLOR) -> QIcon:
    """The icon for windows and the taskbar button."""
    icon = QIcon()
    for size in ICO_SIZES:
        icon.addPixmap(QPixmap.fromImage(render_app_tile(size, color)))
    return icon


def _png_bytes(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def save_app_icon(path: Path, color: str = APP_COLOR) -> Path:
    """Write a multi-resolution .ico with a hand-drawn image per size.

    Qt's ICO writer stores a single image, which left Windows scaling one
    256px bitmap down to 16px for the title bar and Start menu. This writes the
    format directly - an ICONDIR header, one directory entry per size, and
    PNG-compressed image data, which Windows has read since Vista.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [(size, _png_bytes(render_app_tile(size, color))) for size in ICO_SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    directory = b""
    blobs = b""
    for size, png in images:
        dimension = 0 if size >= 256 else size  # 0 means 256 in the format
        directory += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(png), offset
        )
        blobs += png
        offset += len(png)

    path.write_bytes(header + directory + blobs)
    return path
