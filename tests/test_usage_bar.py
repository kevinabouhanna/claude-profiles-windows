"""Tests for the quota bar.

The paint path was at 48% and had no assertions behind it. It is the element
the user actually reads a number from, and it takes values straight from
claude-swap, so it has to survive whatever arrives - including the values a
sane API should never send.
"""

from __future__ import annotations

import pytest

from claude_profiles.models import UsageWindow
from claude_profiles.widgets import theme
from claude_profiles.widgets.usage_bar import (
    CRITICAL_PCT,
    WARN_PCT,
    UsageBar,
    fill_color,
)

pytestmark = pytest.mark.usefixtures("qapp")

ACCENT = "#0078D4"


@pytest.fixture(autouse=True)
def tokens():
    theme.refresh_tokens()


# --- colour escalation ----------------------------------------------------


def test_normal_usage_keeps_the_profile_colour():
    """Below the warning line the bar should still say which profile it is."""
    assert fill_color(0, ACCENT) == theme.tokens().on_surface(ACCENT)
    assert fill_color(WARN_PCT - 0.1, ACCENT) == theme.tokens().on_surface(ACCENT)


def test_the_warning_band_uses_the_caution_colour():
    assert fill_color(WARN_PCT, ACCENT) == theme.tokens().caution
    assert fill_color(CRITICAL_PCT - 0.1, ACCENT) == theme.tokens().caution


def test_the_critical_band_uses_the_critical_colour():
    assert fill_color(CRITICAL_PCT, ACCENT) == theme.tokens().critical
    assert fill_color(100, ACCENT) == theme.tokens().critical


def test_escalation_is_ordered_and_distinct():
    normal = fill_color(10, ACCENT)
    warn = fill_color(85, ACCENT)
    critical = fill_color(99, ACCENT)
    assert len({normal, warn, critical}) == 3


# --- rendering hostile values ---------------------------------------------


@pytest.mark.parametrize(
    "pct",
    [0.0, 0.4, 50.0, 99.9, 100.0, 150.0, 1e9, -5.0, float("inf"), float("-inf")],
)
def test_any_percentage_paints_without_raising(qtbot, pct):
    """Values come from an external tool; the bar must not be the thing that breaks."""
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=pct, countdown="2h 13m"))
    bar.resize(320, 26)

    bar.grab()  # forces a full paintEvent


def test_a_missing_reading_paints_an_empty_track(qtbot):
    bar = UsageBar("7d", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(None)
    bar.resize(320, 26)

    bar.grab()
    assert "no reading" in bar.toolTip()


def test_an_absurdly_long_countdown_does_not_break_layout(qtbot):
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=50.0, countdown="9" * 300))
    bar.resize(320, 26)

    bar.grab()


def test_a_very_narrow_bar_still_paints(qtbot):
    """The flyout can be resized; the track must not go negative."""
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=75.0, countdown="1h"))
    bar.resize(20, 26)

    bar.grab()


# --- state ----------------------------------------------------------------


def test_a_scoped_window_renames_the_row(qtbot):
    bar = UsageBar("model", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=18.0, name="Opus"))
    assert bar._label == "Opus"


def test_the_tooltip_explains_pace_and_projection(qtbot):
    bar = UsageBar("7d", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(
        UsageWindow(
            pct=63.0,
            countdown="3d 04h",
            clock="09:00",
            expected_pct=57.0,
            ahead_of_pace=True,
            will_last_to_reset=False,
        )
    )

    tip = bar.toolTip()
    assert "63% used" in tip
    assert "Resets in 3d 04h" in tip
    assert "Ahead of pace" in tip
    assert "run out before reset" in tip


def test_a_stale_reading_says_so_in_the_tooltip(qtbot):
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=42.0), stale=True)
    assert "Last known" in bar.toolTip()


def test_changing_the_accent_repaints(qtbot):
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    bar.set_window(UsageWindow(pct=10.0))
    bar.set_accent("#F7630C")
    bar.resize(320, 26)
    bar.grab()
    assert bar._accent == "#F7630C"
