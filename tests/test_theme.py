"""Tests for the Fluent token system.

The load-bearing test here is :func:`test_every_colour_token_is_qcolor_parseable`.
Qt stylesheets accept CSS ``rgba()`` strings but ``QColor`` does not - it
silently returns opaque black instead of failing. That combination produced
black progress tracks and black badge fills while every stylesheet-driven
widget looked correct, so the token values are pinned to solid hex and checked.
"""

from __future__ import annotations

import dataclasses

import pytest
from PySide6.QtGui import QColor

from claude_profiles.widgets import theme

pytestmark = pytest.mark.usefixtures("qapp")

COLOUR_FIELDS = [
    f.name
    for f in dataclasses.fields(theme.Tokens)
    if f.name not in {"dark"}
]


@pytest.fixture(params=[True, False], ids=["dark", "light"])
def tokens(request) -> theme.Tokens:
    return theme._build(request.param, theme.BLUE)


def test_every_colour_token_is_qcolor_parseable(tokens):
    """Any token may be handed to QPainter, so all must parse as a colour."""
    for name in COLOUR_FIELDS:
        value = getattr(tokens, name)
        assert QColor(value).isValid(), f"{name}={value!r} does not parse"


def test_no_token_is_a_css_rgba_string(tokens):
    for name in COLOUR_FIELDS:
        value = getattr(tokens, name)
        assert not value.startswith("rgba("), f"{name} must be solid hex"


def test_tint_helpers_return_parseable_colours(tokens):
    for kind in ("success", "caution", "critical", "unknown"):
        assert QColor(tokens.status_tint(kind)).isValid()
    assert QColor(tokens.accent_tint()).isValid()
    assert QColor(tokens.accent_tint(0.3, theme.ORANGE)).isValid()


def test_dark_and_light_differ_in_text_contrast():
    dark = theme._build(True, theme.BLUE)
    light = theme._build(False, theme.BLUE)
    assert QColor(dark.text).lightnessF() > QColor(light.text).lightnessF()
    assert QColor(dark.card).lightnessF() < QColor(light.card).lightnessF()


def test_text_tones_are_ordered_by_prominence(tokens):
    """Secondary must sit between primary and tertiary, tertiary above disabled."""
    base = QColor(tokens.card).lightnessF()

    def distance(value: str) -> float:
        return abs(QColor(value).lightnessF() - base)

    assert distance(tokens.text) > distance(tokens.text_secondary)
    assert distance(tokens.text_secondary) > distance(tokens.text_tertiary)
    assert distance(tokens.text_tertiary) > distance(tokens.text_disabled)


def test_track_is_visible_against_the_card(tokens):
    """A progress track that matches its surface communicates nothing."""
    assert abs(
        QColor(tokens.track).lightnessF() - QColor(tokens.card).lightnessF()
    ) > 0.03


def test_on_surface_lifts_identity_colour_in_dark_mode():
    dark = theme._build(True, theme.BLUE)
    light = theme._build(False, theme.BLUE)
    assert QColor(dark.on_surface(theme.BLUE)).lightnessF() > QColor(theme.BLUE).lightnessF()
    assert light.on_surface(theme.BLUE) == theme.BLUE


def test_stylesheet_builds_and_mentions_core_controls(tokens, monkeypatch):
    monkeypatch.setattr(theme, "_tokens", tokens)
    css = theme.stylesheet()
    for control in ("QPushButton", "QTabBar::tab", "QCheckBox", "QMenu", "QScrollBar"):
        assert control in css
    assert "rgba(" not in css  # solid hex throughout


def test_button_styles_are_valid_css_fragments(tokens, monkeypatch):
    monkeypatch.setattr(theme, "_tokens", tokens)
    for builder in (
        theme.accent_button_css,
        theme.standard_button_css,
        theme.subtle_button_css,
    ):
        css = builder()
        assert "QPushButton" in css
        assert css.count("{") == css.count("}")


def test_profile_colours_come_from_the_windows_accent_palette():
    """Checks the colours the profiles *use*, not a constant beside them.

    An earlier version asserted on theme.BLUE while the profiles read a second
    definition in models.py with different values, so it passed while the
    profiles were still the old palette.
    """
    from claude_profiles.models import DEFAULT_PROFILES

    colours = {p.key: p.color.upper() for p in DEFAULT_PROFILES}
    assert colours == {"personal": "#0078D4", "work": "#F7630C"}
    assert theme.BLUE.upper() == colours["personal"]


def test_there_is_one_definition_of_each_profile_colour():
    from claude_profiles import models

    assert theme.BLUE is models.BLUE
    assert theme.ORANGE is models.ORANGE
