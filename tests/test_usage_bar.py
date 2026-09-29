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


# --- pace marker ------------------------------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from PySide6.QtGui import QColor, QImage  # noqa: E402

from claude_profiles.models import FIVE_HOURS, SEVEN_DAYS, pace_pct  # noqa: E402
from claude_profiles.widgets.usage_bar import LABEL_WIDTH, VALUE_WIDTH  # noqa: E402

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _window(pct=40.0, resets_in=timedelta(hours=3), **extra):
    return UsageWindow(pct=pct, resets_at=NOW + resets_in, **extra)


def test_the_five_hour_pace_is_the_share_of_the_window_elapsed():
    """Three hours left of five means two have passed: even spending is 40%."""
    assert pace_pct(_window(resets_in=timedelta(hours=3)), FIVE_HOURS, NOW) == pytest.approx(40.0)
    assert pace_pct(_window(resets_in=timedelta(hours=5)), FIVE_HOURS, NOW) == pytest.approx(0.0)
    assert pace_pct(_window(resets_in=timedelta(0)), FIVE_HOURS, NOW) == pytest.approx(100.0)


def test_the_seven_day_pace_works_the_same_way():
    window = _window(resets_in=timedelta(days=3, hours=12))
    assert pace_pct(window, SEVEN_DAYS, NOW) == pytest.approx(50.0)


def test_claude_swaps_own_expected_percentage_wins():
    window = _window(resets_in=timedelta(hours=3), expected_pct=57.0)
    assert pace_pct(window, FIVE_HOURS, NOW) == 57.0


@pytest.mark.parametrize(
    "window",
    [
        None,
        UsageWindow(pct=10.0),  # no reset time
        _window(resets_in=timedelta(hours=9)),  # reset further off than the window is long
        _window(resets_in=timedelta(minutes=-5)),  # already past
    ],
)
def test_no_marker_when_the_pace_cannot_be_placed(window):
    assert pace_pct(window, FIVE_HOURS, NOW) is None


def test_a_window_of_unknown_length_gets_no_computed_marker():
    """Per-model rows do not say how long their window is."""
    assert pace_pct(_window(), None, NOW) is None


def test_a_naive_reset_time_is_read_as_utc():
    naive = UsageWindow(pct=10.0, resets_at=(NOW + timedelta(hours=3)).replace(tzinfo=None))
    assert pace_pct(naive, FIVE_HOURS, NOW) == pytest.approx(40.0)


def _render(bar: UsageBar) -> QImage:
    bar.resize(400, bar.height())
    image = QImage(bar.size(), QImage.Format.Format_ARGB32)
    image.fill(QColor(theme.tokens().background))
    bar.render(image)
    return image


@pytest.mark.parametrize("compact", [True, False])
def test_the_marker_is_drawn_in_the_flyout_and_the_dashboard(qtbot, compact):
    """It used to be hidden in the compact flyout, where people look most."""
    bar = UsageBar("5h", ACCENT, compact=compact)
    qtbot.addWidget(bar)
    bar.set_window(
        UsageWindow(pct=10.0, expected_pct=50.0, countdown="2h 30m"), length_seconds=FIVE_HOURS
    )
    image = _render(bar)

    track_x = LABEL_WIDTH + 10
    track_right = 400 - VALUE_WIDTH - 104 - 10
    marker_x = int(track_x + (track_right - track_x) * 0.5)
    above_track = int(bar.height() / 2 - 2 - 3)  # inside the overhang, clear of the track
    colour = QColor(image.pixel(marker_x, above_track))
    background = QColor(theme.tokens().background)
    assert abs(colour.lightness() - background.lightness()) > 60, "no visible pace line"


def test_the_tooltip_explains_the_five_hour_pace(qtbot):
    bar = UsageBar("5h", ACCENT)
    qtbot.addWidget(bar)
    soon = datetime.now(UTC) + timedelta(hours=1)  # four of five hours gone: 80%
    bar.set_window(UsageWindow(pct=90.0, resets_at=soon), length_seconds=FIVE_HOURS)
    assert "Ahead of pace" in bar.toolTip()
    assert "80%" in bar.toolTip()
