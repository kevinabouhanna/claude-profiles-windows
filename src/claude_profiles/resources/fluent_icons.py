"""Native Windows iconography via the Segoe Fluent Icons font.

Windows 11 draws its own UI from **Segoe Fluent Icons**, and Windows 10 from
**Segoe MDL2 Assets**. Using the same font means the app's icons are the real
system icons at any DPI, monochrome and theme-aware - not emoji, and not
bitmaps that blur when scaled.

Every glyph named here was rendered and visually verified against the installed
font rather than taken from memory.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap

FLUENT_FAMILY = "Segoe Fluent Icons"
MDL2_FAMILY = "Segoe MDL2 Assets"

# Semantic name -> codepoint. Both fonts share this range, so one table serves
# Windows 10 and 11 alike.
GLYPHS: dict[str, str] = {
    "refresh": "",
    "settings": "",
    "close": "",
    "cancel": "",  # lighter cross, for lists
    "personal": "",  # Contact
    "work": "",  # Work (briefcase)
    "success": "",  # Completed
    "check": "",  # Accept
    "warning": "",
    "error": "",  # ErrorBadge
    "info": "",
    "folder": "",  # FolderFill
    "terminal": "",  # CommandPrompt
    "switch": "",
    "open_window": "",  # OpenInNewWindow
    "lock": "",
    "shield": "",
    "timer": "",  # Stopwatch
    "history": "",
    "add": "",
    "add_account": "",  # AccountPic (person +)
    "power": "",
    "chart": "",  # Diagnostic
    "chevron_right": "",
    "chevron_up": "",
    "chevron_down": "",
    "delete": "",
    "edit": "",
    "view": "",
    "document": "",
    "people": "",
    "play": "",
    "switch_user": "",  # person with swap arrows - the app mark
    "accounts": "",  # two people
    "home": "",
    "keyboard": "",
    "bell": "",
    "minus": "",
    "clock": "",
}


@lru_cache(maxsize=1)
def icon_family() -> str | None:
    """The best available system icon font, or None if neither is installed."""
    families = set(QFontDatabase.families())
    for family in (FLUENT_FAMILY, MDL2_FAMILY):
        if family in families:
            return family
    return None


@lru_cache(maxsize=32)
def icon_font(size: int = 14) -> QFont:
    font = QFont(icon_family() or "Segoe UI", size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def glyph(name: str) -> str:
    """The raw character for ``name``, or an empty string if unavailable."""
    if icon_family() is None:
        return ""
    return GLYPHS.get(name, "")


def draw_glyph(
    painter: QPainter, rect: QRect, name: str, color: str, size: int = 14
) -> None:
    """Paint an icon inside ``rect``, centred."""
    char = glyph(name)
    if not char:
        return
    painter.save()
    painter.setFont(icon_font(size))
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, char)
    painter.restore()


def pixmap(name: str, color: str, size: int = 16, scale: int = 2) -> QPixmap:
    """A monochrome icon pixmap, rendered at ``scale`` for crisp HiDPI output."""
    physical = size * scale
    pix = QPixmap(physical, physical)
    pix.fill(Qt.GlobalColor.transparent)
    char = glyph(name)
    if not char:
        return pix

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # Fluent glyphs are designed on a 16px em; scale the point size with the box.
    painter.setFont(icon_font(int(physical * 0.72)))
    painter.setPen(QColor(color))
    painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, char)
    painter.end()

    pix.setDevicePixelRatio(float(scale))
    return pix


def icon(name: str, color: str, size: int = 16) -> QIcon:
    """A QIcon for menus, buttons, and tabs."""
    return QIcon(pixmap(name, color, size))


@lru_cache(maxsize=64)
def icon_path(name: str, color: str, size: int = 12) -> str:
    """Write an icon to the cache directory and return a QSS-safe ``url()`` path.

    Qt stylesheets can only reference indicator images by URL, so the glyphs
    used for checkbox ticks and spin-button arrows have to exist as files. They
    are generated UI assets, not user data, so they live in the cache location
    rather than the app's data folder.
    """
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    directory = Path(base or Path.home() / ".cache") / "icons"
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ""

    safe_color = color.lstrip("#")
    target = directory / f"{name}-{safe_color}-{size}.png"
    if not target.is_file():
        try:
            pixmap(name, color, size, scale=2).save(str(target), "PNG")
        except OSError:
            return ""
    # QSS wants forward slashes even on Windows.
    return str(target).replace("\\", "/")


def icon_size(size: int = 16) -> QSize:
    return QSize(size, size)


def available() -> bool:
    return icon_family() is not None
