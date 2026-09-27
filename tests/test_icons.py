"""Tests for the tray and app icons.

The old tray icon looked soft for a concrete, checkable reason: its glyph was
drawn with subpixel (ClearType) antialiasing into a bitmap, which leaves pink
and purple fringes on what should be a white shape. At 16x16 those fringes are
a large share of the pixels. ``test_the_tray_glyph_has_no_colour_fringing``
guards that directly.
"""

from __future__ import annotations

import struct

import pytest
from PySide6.QtGui import QColor

from claude_profiles.resources import icons

pytestmark = pytest.mark.usefixtures("qapp")

BLUE = "#0078D4"
ORANGE = "#F7630C"


def _opaque_pixels(image):
    for y in range(image.height()):
        for x in range(image.width()):
            colour = QColor(image.pixelColor(x, y))
            if colour.alpha() > 40:
                yield x, y, colour


# --- tray -----------------------------------------------------------------


def test_every_tray_size_is_drawn_natively():
    """Windows asks for 16px at 100% scaling, 20 at 125%, 24 at 150%..."""
    icon = icons.tray_icon(dark_taskbar=True)
    available = {size.width() for size in icon.availableSizes()}
    for size in (16, 20, 24, 32):
        assert size in available, f"no native {size}px image; Windows would rescale"


@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_the_tray_glyph_has_no_colour_fringing(size):
    """A monochrome glyph must be greyscale in every pixel."""
    image = icons.render_tray(size, None, dark_taskbar=True)
    fringed = [
        (x, y)
        for x, y, c in _opaque_pixels(image)
        if max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue()) > 12
    ]
    assert not fringed, f"{len(fringed)} coloured pixels in a monochrome glyph"


def test_the_glyph_is_light_on_a_dark_taskbar():
    image = icons.render_tray(16, None, dark_taskbar=True)
    lightness = [c.lightnessF() for _, _, c in _opaque_pixels(image)]
    assert lightness and max(lightness) > 0.9


def test_the_glyph_is_dark_on_a_light_taskbar():
    """Windows 11's own tray icons turn black on a light taskbar."""
    image = icons.render_tray(16, None, dark_taskbar=False)
    lightness = [c.lightnessF() for _, _, c in _opaque_pixels(image)]
    assert lightness and min(lightness) < 0.2


def test_the_glyph_fills_the_cell():
    """The old icon shrank the glyph to roughly ten pixels inside a tile."""
    image = icons.render_tray(16, None, dark_taskbar=True)
    xs = [x for x, _, _ in _opaque_pixels(image)]
    ys = [y for _, y, _ in _opaque_pixels(image)]
    assert max(xs) - min(xs) >= 13
    assert max(ys) - min(ys) >= 13


def _has_colour_near(image, hue_color: str, region) -> bool:
    target = QColor(hue_color)
    x0, y0, x1, y1 = region
    for y in range(y0, y1):
        for x in range(x0, x1):
            c = QColor(image.pixelColor(x, y))
            same_hue = abs(c.hslHue() - target.hslHue()) < 12
            if c.alpha() > 200 and same_hue and c.hslSaturation() > 120:
                return True
    return False


@pytest.mark.parametrize("size", [16, 20, 24, 32])
def test_the_badge_shows_the_active_profile_top_right(size):
    image = icons.render_tray(size, ORANGE, dark_taskbar=True)
    corner = (size // 2, 0, size, size // 2)
    assert _has_colour_near(image, ORANGE, corner)


def test_no_badge_without_an_active_profile():
    image = icons.render_tray(16, None, dark_taskbar=True)
    assert not _has_colour_near(image, BLUE, (0, 0, 16, 16))
    assert not _has_colour_near(image, ORANGE, (0, 0, 16, 16))


def test_the_badge_is_separated_from_the_glyph():
    """A knockout ring keeps the badge from merging into the glyph."""
    with_badge = icons.render_tray(24, BLUE, dark_taskbar=True)
    diameter = max(5, round(24 * 0.30))
    gap = max(1, 24 // 16)
    centre_x, centre_y = 24 - diameter / 2, diameter / 2
    ring_radius = diameter / 2 + gap / 2
    # Sample the ring due left of the badge centre: it must be transparent.
    x = int(centre_x - ring_radius)
    y = int(centre_y)
    assert QColor(with_badge.pixelColor(x, y)).alpha() < 128


def test_taskbar_theme_detection_returns_a_bool():
    from claude_profiles.widgets.theme import taskbar_is_dark

    assert isinstance(taskbar_is_dark(), bool)


# --- app icon -------------------------------------------------------------


def test_the_app_tile_is_coloured():
    image = icons.render_app_tile(32)
    centre_top = QColor(image.pixelColor(16, 3))
    assert centre_top.hslSaturation() > 100, "the app icon should be a coloured tile"


def test_the_app_icon_carries_every_size():
    available = {s.width() for s in icons.app_icon().availableSizes()}
    for size in (16, 24, 32, 48, 256):
        assert size in available


# --- the .ico file --------------------------------------------------------


def test_the_ico_holds_one_image_per_size(tmp_path):
    """Qt's own ICO writer stores one image; Windows then scales it for 16px."""
    path = icons.save_app_icon(tmp_path / "app.ico")
    data = path.read_bytes()

    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert (reserved, kind) == (0, 1), "not an icon file"
    assert count == len(icons.ICO_SIZES)

    seen = []
    for index in range(count):
        width, height, _, _, planes, bits, length, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + 16 * index
        )
        size = 256 if width == 0 else width
        seen.append(size)
        assert width == height
        assert (planes, bits) == (1, 32)
        assert data[offset : offset + 8] == b"\x89PNG\r\n\x1a\n", "entry is not PNG data"
        assert offset + length <= len(data)
    assert sorted(seen) == sorted(icons.ICO_SIZES)


def test_windows_can_read_the_ico(tmp_path):
    """Round-trip through Qt's reader as a stand-in for the shell."""
    from PySide6.QtGui import QIcon

    path = icons.save_app_icon(tmp_path / "app.ico")
    loaded = QIcon(str(path))
    assert not loaded.isNull()
    assert not loaded.pixmap(16, 16).isNull()
