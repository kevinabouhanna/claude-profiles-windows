"""Fluent Design tokens: colour, type ramp, radii, and the global stylesheet.

Values follow the WinUI 2/Fluent 2 resource names so they read the same as the
platform documentation - ``TextFillColorSecondary``, ``CardStrokeColorDefault``
and so on - rather than being invented per widget.

Two things make the result look native rather than merely dark:

* **Layered neutrals.** Fluent surfaces are translucent white or black laid over
  a base, not arbitrary greys. Colours here are composited from the same alphas
  the platform uses, so cards sit at the right depth.
* **The system accent.** Qt reports the user's Windows accent colour through
  the palette, so the app picks it up instead of hard-coding a blue.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QWidget

# -- type ramp -------------------------------------------------------------

FONT_TEXT = "Segoe UI Variable Text"
FONT_DISPLAY = "Segoe UI Variable Display"
FONT_FALLBACK = "Segoe UI"

CAPTION = 12
BODY = 14
BODY_LARGE = 18
SUBTITLE = 20
TITLE = 28

# -- geometry (Fluent uses a 4px grid) -------------------------------------

RADIUS_CONTROL = 4
RADIUS_CARD = 8
CONTROL_HEIGHT = 32

# -- profile identity ------------------------------------------------------
# Drawn from the Windows accent palette rather than a generic web palette, so
# they sit naturally next to system chrome.
# Profile identity colours live in models (the source of truth); re-exported
# here so there is exactly one definition. There used to be two, and the
# profiles used the other one.
from ..models import BLUE, ORANGE  # noqa: E402,F401


def _composite(base: str, overlay: tuple[int, int, int] | str, alpha: float) -> str:
    """Flatten a translucent overlay onto an opaque base, returning solid hex.

    Every colour this module exposes is pre-composited rather than left as a
    CSS ``rgba()`` string. Qt stylesheets accept ``rgba()``, but ``QColor`` does
    **not** parse it - it silently yields opaque black. Mixing the two means
    stylesheet-driven widgets look right while anything drawn with QPainter
    (progress tracks, badges, borders) renders black. Solid hex works correctly
    in both, so the distinction cannot be got wrong at a call site.
    """
    bottom = QColor(base)
    if isinstance(overlay, str):
        top = QColor(overlay)
        overlay = (top.red(), top.green(), top.blue())
    r = round(bottom.red() * (1 - alpha) + overlay[0] * alpha)
    g = round(bottom.green() * (1 - alpha) + overlay[1] * alpha)
    b = round(bottom.blue() * (1 - alpha) + overlay[2] * alpha)
    return QColor(r, g, b).name()


def _shift(color: str, amount: float) -> str:
    """Lighten (positive) or darken (negative) a colour in HSL space."""
    c = QColor(color)
    h, s, lightness, a = c.getHslF()
    return QColor.fromHslF(h, s, max(0.0, min(1.0, lightness + amount)), a).name()


@dataclass(frozen=True)
class Tokens:
    dark: bool
    accent: str

    # surfaces
    background: str
    card: str
    card_hover: str
    control: str
    control_hover: str
    control_pressed: str
    subtle_hover: str

    # strokes
    stroke: str
    card_stroke: str
    divider: str
    track: str

    # text
    text: str
    text_secondary: str
    text_tertiary: str
    text_disabled: str
    text_on_accent: str

    # status
    success: str
    caution: str
    critical: str

    def status_tint(self, kind: str, alpha: float = 0.16) -> str:
        """A status colour laid over the card surface, as solid hex."""
        color = {
            "success": self.success,
            "caution": self.caution,
            "critical": self.critical,
        }.get(kind)
        if color is None:
            return self.control
        return _composite(self.card, color, alpha)

    def accent_tint(self, alpha: float = 0.16, color: str | None = None) -> str:
        return _composite(self.card, color or self.accent, alpha)

    def on_surface(self, color: str) -> str:
        """Lift an identity colour for legibility on a dark surface."""
        return _shift(color, 0.12) if self.dark else color


_WHITE = (255, 255, 255)
_BLACK = (0, 0, 0)


def _build(dark: bool, accent: str) -> Tokens:
    """Compose the Fluent alpha values onto concrete surfaces.

    Text and lines composite against the *card*, since that is where nearly all
    content sits; the few pixels drawn straight on the window background differ
    imperceptibly at these alphas.
    """
    if dark:
        background = "#202020"
        card = _composite(background, _WHITE, 0.0512)
        return Tokens(
            dark=True,
            accent=accent,
            background=background,
            card=card,
            card_hover=_composite(background, _WHITE, 0.0837),
            control=_composite(card, _WHITE, 0.0605),
            control_hover=_composite(card, _WHITE, 0.0837),
            control_pressed=_composite(card, _WHITE, 0.0326),
            subtle_hover=_composite(background, _WHITE, 0.0605),
            stroke=_composite(card, _WHITE, 0.0930),
            card_stroke=_composite(background, _WHITE, 0.0578),
            divider=_composite(card, _WHITE, 0.0837),
            # ControlStrongFillColorDisabled - the WinUI progress-track fill.
            track=_composite(card, _WHITE, 0.1581),
            text="#FFFFFF",
            text_secondary=_composite(card, _WHITE, 0.786),
            text_tertiary=_composite(card, _WHITE, 0.5442),
            text_disabled=_composite(card, _WHITE, 0.3628),
            text_on_accent="#FFFFFF",
            success="#6CCB5F",
            caution="#FCE100",
            critical="#FF99A4",
        )
    background = "#F3F3F3"
    card = "#FFFFFF"
    return Tokens(
        dark=False,
        accent=accent,
        background=background,
        card=card,
        card_hover=_composite(card, _BLACK, 0.0373),
        control=_composite(card, _BLACK, 0.0241),
        control_hover=_composite(card, _BLACK, 0.0373),
        control_pressed=_composite(card, _BLACK, 0.0578),
        subtle_hover=_composite(background, _BLACK, 0.0373),
        stroke=_composite(card, _BLACK, 0.0578),
        card_stroke=_composite(background, _BLACK, 0.0578),
        divider=_composite(card, _BLACK, 0.0803),
        track=_composite(card, _BLACK, 0.2169),
        text="#1A1A1A",
        text_secondary=_composite(card, _BLACK, 0.6063),
        text_tertiary=_composite(card, _BLACK, 0.4458),
        text_disabled=_composite(card, _BLACK, 0.3614),
        text_on_accent="#FFFFFF",
        success="#0F7B0F",
        caution="#9D5D00",
        critical="#C42B1C",
    )


_tokens: Tokens | None = None


def tokens() -> Tokens:
    """Current theme tokens, derived from the live Qt palette."""
    global _tokens
    if _tokens is None:
        refresh_tokens()
    assert _tokens is not None
    return _tokens


def refresh_tokens() -> Tokens:
    """Re-read the system theme. Call on startup and on a theme change."""
    global _tokens
    app = QGuiApplication.instance()
    dark = True
    accent = BLUE
    if app is not None:
        palette = app.palette()
        window = palette.color(QPalette.ColorRole.Window)
        dark = window.lightnessF() < 0.5
        highlight = palette.color(QPalette.ColorRole.Highlight)
        if highlight.isValid() and highlight.saturationF() > 0.05:
            accent = highlight.name()
    _tokens = _build(dark, accent)
    return _tokens


def taskbar_is_dark() -> bool:
    """Whether the Windows *taskbar* is dark.

    Windows keeps two theme settings: one for apps and one for the taskbar and
    Start ("Choose your default Windows mode"). They can differ, and the tray
    icon sits on the taskbar, so it must follow the second. Read-only: this
    app never writes to the registry.
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return int(value) == 0
    except (OSError, ImportError, ValueError):
        return tokens().dark


# -- fonts -----------------------------------------------------------------


@lru_cache(maxsize=32)
def font(size: int = BODY, weight: int = QFont.Weight.Normal) -> QFont:
    """A font from the Fluent type ramp.

    Segoe UI Variable has separate optical sizes: Display above 18px, Text at or
    below. Falling back to Segoe UI keeps Windows 10 looking right.
    """
    from PySide6.QtGui import QFontDatabase

    families = set(QFontDatabase.families())
    preferred = FONT_DISPLAY if size > BODY_LARGE else FONT_TEXT
    family = preferred if preferred in families else FONT_FALLBACK
    f = QFont(family, size)
    f.setPixelSize(size)
    f.setWeight(QFont.Weight(weight))
    return f


def apply_app_font(app) -> None:
    app.setFont(font(BODY))


# -- convenience CSS -------------------------------------------------------


def text_css(size: int = BODY, tone: str = "primary", weight: int = 400) -> str:
    t = tokens()
    color = {
        "primary": t.text,
        "secondary": t.text_secondary,
        "tertiary": t.text_tertiary,
        "disabled": t.text_disabled,
        "accent": t.accent,
    }.get(tone, t.text)
    return f"font-size: {size}px; font-weight: {weight}; color: {color};"


def accent_button_css(color: str | None = None) -> str:
    """Fluent accent button: filled, 4px radius, 32px tall, regular weight."""
    t = tokens()
    base = color or t.accent
    return (
        f"QPushButton {{ background-color: {base}; color: {t.text_on_accent};"
        f" border: 1px solid {_shift(base, 0.06)}; border-radius: {RADIUS_CONTROL}px;"
        f" padding: 0 12px; min-height: {CONTROL_HEIGHT}px; font-size: {BODY}px;"
        " font-weight: 600; }"
        f"QPushButton:hover {{ background-color: {_shift(base, 0.05)}; }}"
        f"QPushButton:pressed {{ background-color: {_shift(base, -0.05)}; }}"
        f"QPushButton:disabled {{ background-color: {t.control};"
        f" color: {t.text_disabled}; border-color: {t.stroke}; }}"
    )


def standard_button_css() -> str:
    t = tokens()
    return (
        f"QPushButton {{ background-color: {t.control}; color: {t.text};"
        f" border: 1px solid {t.stroke}; border-radius: {RADIUS_CONTROL}px;"
        f" padding: 0 12px; min-height: {CONTROL_HEIGHT}px; font-size: {BODY}px; }}"
        f"QPushButton:hover {{ background-color: {t.control_hover}; }}"
        f"QPushButton:pressed {{ background-color: {t.control_pressed};"
        f" color: {t.text_secondary}; }}"
        f"QPushButton:disabled {{ color: {t.text_disabled};"
        f" background-color: {t.control_pressed}; }}"
    )


def subtle_button_css() -> str:
    """Transparent until hovered - Fluent's icon-button treatment."""
    t = tokens()
    return (
        f"QPushButton {{ background-color: transparent; color: {t.text};"
        f" border: none; border-radius: {RADIUS_CONTROL}px; padding: 0 8px; }}"
        f"QPushButton:hover {{ background-color: {t.subtle_hover}; }}"
        f"QPushButton:pressed {{ background-color: {t.control_pressed};"
        f" color: {t.text_secondary}; }}"
        f"QPushButton:disabled {{ color: {t.text_disabled}; }}"
    )


def card_css(object_name: str, *, border: str | None = None) -> str:
    t = tokens()
    return (
        f"#{object_name} {{ background-color: {t.card};"
        f" border: 1px solid {border or t.card_stroke};"
        f" border-radius: {RADIUS_CARD}px; }}"
    )


def stylesheet() -> str:
    """Global QSS so every standard control reads as Fluent."""
    from ..resources import fluent_icons

    t = tokens()
    # Qt can only draw indicator glyphs from image URLs, so these are rendered
    # from the system icon font to cached PNGs.
    check = fluent_icons.icon_path("check", t.text_on_accent, 12)
    chevron_down = fluent_icons.icon_path("chevron_down", t.text_secondary, 10)
    chevron_up = fluent_icons.icon_path("chevron_up", t.text_secondary, 10)
    return f"""
QWidget {{
    color: {t.text};
    font-size: {BODY}px;
}}
QToolTip {{
    background-color: {t.card};
    color: {t.text};
    border: 1px solid {t.stroke};
    border-radius: {RADIUS_CONTROL}px;
    padding: 6px 8px;
}}
{standard_button_css()}
QGroupBox {{
    background-color: {t.card};
    border: 1px solid {t.card_stroke};
    border-radius: {RADIUS_CARD}px;
    margin-top: 10px;
    padding: 14px 16px 16px 16px;
    font-size: {BODY}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: {t.text_secondary};
}}
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar {{
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {t.text_secondary};
    border: none;
    padding: 8px 14px;
    margin-right: 4px;
    border-radius: {RADIUS_CONTROL}px;
    font-size: {BODY}px;
}}
QTabBar::tab:hover {{
    background-color: {t.subtle_hover};
    color: {t.text};
}}
QTabBar::tab:selected {{
    background-color: {t.card};
    color: {t.text};
    font-weight: 600;
}}
QLineEdit, QSpinBox, QComboBox {{
    background-color: {t.control};
    border: 1px solid {t.stroke};
    border-bottom: 2px solid {t.divider};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px 10px;
    min-height: {CONTROL_HEIGHT - 10}px;
    color: {t.text};
    selection-background-color: {t.accent};
}}
QSpinBox:focus, QComboBox:focus, QLineEdit:focus {{
    border-bottom: 2px solid {t.accent};
    background-color: {t.control_hover};
}}
QSpinBox:disabled, QComboBox:disabled {{
    color: {t.text_disabled};
}}
QComboBox::drop-down {{
    border: none;
    width: 30px;
}}
QComboBox::down-arrow {{
    image: url({chevron_down});
    width: 10px;
    height: 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {t.card};
    border: 1px solid {t.stroke};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    outline: none;
    selection-background-color: {t.subtle_hover};
    selection-color: {t.text};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 26px;
    border-radius: {RADIUS_CONTROL}px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {t.control_hover};
}}
QSpinBox::up-arrow {{
    image: url({chevron_up});
    width: 9px;
    height: 9px;
}}
QSpinBox::down-arrow {{
    image: url({chevron_down});
    width: 9px;
    height: 9px;
}}
QCheckBox {{
    spacing: 10px;
    color: {t.text};
    padding: 3px 0;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: {RADIUS_CONTROL}px;
    border: 1px solid {t.text_tertiary};
    background-color: {t.control};
}}
QCheckBox::indicator:hover {{
    background-color: {t.control_hover};
}}
QCheckBox::indicator:checked {{
    background-color: {t.accent};
    border-color: {t.accent};
    image: url({check});
}}
QCheckBox::indicator:checked:hover {{
    background-color: {_shift(t.accent, 0.06)};
}}
QListWidget {{
    background-color: transparent;
    border: 1px solid {t.card_stroke};
    border-radius: {RADIUS_CONTROL}px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 7px 8px;
    border-radius: {RADIUS_CONTROL}px;
    color: {t.text_secondary};
}}
QListWidget::item:hover {{
    background-color: {t.subtle_hover};
}}
QListWidget::item:selected {{
    background-color: {t.subtle_hover};
    color: {t.text};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {t.text_disabled};
    border-radius: 3px;
    min-height: 28px;
    margin: 0 4px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {t.text_tertiary};
}}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
    border: none;
    height: 0;
}}
QMenu {{
    background-color: {t.card};
    border: 1px solid {t.stroke};
    border-radius: {RADIUS_CARD}px;
    padding: 4px;
}}
QMenu::item {{
    padding: 8px 28px 8px 12px;
    border-radius: {RADIUS_CONTROL}px;
    color: {t.text};
}}
QMenu::item:selected {{
    background-color: {t.subtle_hover};
}}
QMenu::item:disabled {{
    color: {t.text_tertiary};
}}
QMenu::separator {{
    height: 1px;
    background-color: {t.divider};
    margin: 4px 8px;
}}
QMenu::icon {{
    padding-left: 10px;
}}
"""


# -- legacy helpers kept so older call sites stay valid ---------------------


def muted(widget: QWidget | None = None) -> str:
    return tokens().text_secondary


def faint(widget: QWidget | None = None) -> str:
    return tokens().text_disabled


def hairline(widget: QWidget | None = None) -> str:
    return tokens().stroke


def muted_label_css(widget: QWidget | None = None, size: int = CAPTION) -> str:
    return text_css(size, "secondary")
