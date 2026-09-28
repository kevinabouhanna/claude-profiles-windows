"""Tests for threshold notifications.

This module was at 45% coverage with no tests of its own, and it is real
decision logic: it chooses when to interrupt the user. Getting it wrong means
either silence when a quota is nearly gone, or the same alert every two
minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claude_profiles.models import (
    BLUE,
    Account,
    Profile,
    ProfileState,
    Usage,
    UsageStatus,
    UsageWindow,
)
from claude_profiles.services.notification_service import (
    NotificationService,
    ThresholdTracker,
)

pytestmark = pytest.mark.usefixtures("qapp")


class FakeTray:
    """Stands in for QSystemTrayIcon."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.messages: list[tuple[str, str]] = []

    def isSystemTrayAvailable(self) -> bool:  # noqa: N802 - Qt naming
        return self.available

    def showMessage(self, title, message, icon=None, msecs=0) -> None:  # noqa: N802
        self.messages.append((title, message))


def state(key: str, five: float | None, seven: float = 0.0, *, stale: bool = False,
          status: UsageStatus = UsageStatus.OK) -> ProfileState:
    profile = Profile(key=key, name=key.title(), alias=key, color=BLUE, number=1)
    usage = Usage(
        five_hour=UsageWindow(pct=five) if five is not None else None,
        seven_day=UsageWindow(pct=seven),
    )
    account = Account(
        email=f"{key}@example.invalid",
        number=1,
        alias=key,
        active=True,
        usage_status=status,
        usage=None if stale else usage,
        last_good_usage=usage if stale else None,
        last_good_fetched_at=datetime.now(UTC) if stale else None,
    )
    return ProfileState(profile=profile, account=account)


@pytest.fixture
def service():
    def _make(enabled: bool = True, warn: int = 80, critical: int = 95):
        tray = FakeTray()
        svc = NotificationService(tray, enabled)
        svc.configure(enabled=enabled, warn_pct=warn, critical_pct=critical)
        return svc, tray

    return _make


# --- the latch ------------------------------------------------------------


def test_crossing_the_warning_threshold_reports_once():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)

    assert tracker.check("personal", "5h", 79) is None
    assert tracker.check("personal", "5h", 81) == 1
    assert tracker.check("personal", "5h", 85) is None, "must not repeat"
    assert tracker.check("personal", "5h", 94) is None


def test_crossing_critical_after_warning_reports_again():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("personal", "5h", 85) == 1
    assert tracker.check("personal", "5h", 96) == 2
    assert tracker.check("personal", "5h", 99) is None


def test_jumping_straight_past_both_reports_critical_only():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("personal", "5h", 99) == 2


def test_a_quota_reset_rearms_the_alert():
    """After a reset the same threshold must be able to fire again."""
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("personal", "5h", 96) == 2

    assert tracker.check("personal", "5h", 2) is None  # window reset

    assert tracker.check("personal", "5h", 85) == 1
    assert tracker.check("personal", "5h", 97) == 2


def test_dropping_to_warning_rearms_only_critical():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    tracker.check("personal", "5h", 96)

    assert tracker.check("personal", "5h", 85) is None, "still above warn"
    assert tracker.check("personal", "5h", 96) == 2


def test_boundaries_are_inclusive():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("a", "5h", 80) == 1
    assert tracker.check("b", "5h", 95) == 2


def test_windows_and_profiles_latch_independently():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("personal", "5h", 85) == 1
    assert tracker.check("personal", "7d", 85) == 1, "7d has its own latch"
    assert tracker.check("work", "5h", 85) == 1, "work has its own latch"


def test_a_missing_reading_is_not_a_crossing():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    assert tracker.check("personal", "5h", None) is None


def test_reset_clears_every_latch():
    tracker = ThresholdTracker(warn_pct=80, critical_pct=95)
    tracker.check("personal", "5h", 96)
    tracker.reset()
    assert tracker.check("personal", "5h", 96) == 2


# --- reconfiguration ------------------------------------------------------


def test_lowering_the_threshold_can_fire_for_current_usage(service):
    svc, tray = service(warn=80)
    svc.check_thresholds((state("personal", 70),))
    assert tray.messages == []

    svc.configure(enabled=True, warn_pct=60, critical_pct=95)
    svc.check_thresholds((state("personal", 70),))

    assert len(tray.messages) == 1


def test_raising_the_threshold_rearms_rather_than_repeating(service):
    svc, tray = service(warn=80, critical=95)
    svc.check_thresholds((state("personal", 96),))
    assert len(tray.messages) == 1

    svc.configure(enabled=True, warn_pct=80, critical_pct=99)
    svc.check_thresholds((state("personal", 96),))
    assert len(tray.messages) == 1, "96 is no longer critical; do not repeat"

    svc.check_thresholds((state("personal", 99),))
    assert len(tray.messages) == 2


# --- the service ----------------------------------------------------------


def test_nothing_is_shown_when_notifications_are_off(service):
    svc, tray = service(enabled=False)
    svc.check_thresholds((state("personal", 99),))
    assert tray.messages == []


def test_a_switch_confirmation_ignores_the_setting(service):
    """A direct result of a click is not a passive alert."""
    svc, tray = service(enabled=False)
    svc.notify_switch("Work", "work@example.invalid")
    assert len(tray.messages) == 1


def test_stale_readings_do_not_raise_alerts(service):
    """Alerting on a retained reading reports a threshold that may be hours old."""
    svc, tray = service()
    svc.check_thresholds((state("personal", 99, stale=True),))
    assert tray.messages == []


def test_an_unregistered_profile_is_skipped(service):
    svc, tray = service()
    profile = Profile(key="personal", name="Personal", alias="personal", color=BLUE)
    svc.check_thresholds((ProfileState(profile=profile, account=None),))
    assert tray.messages == []


def test_both_windows_can_alert(service):
    svc, tray = service()
    svc.check_thresholds((state("personal", 96, seven=97),))
    assert len(tray.messages) == 2


def test_the_message_names_the_profile_window_and_level(service):
    svc, tray = service()
    svc.check_thresholds((state("work", 96),))

    title, body = tray.messages[0]
    assert "Work" in title
    assert "5h" in title
    assert "critical" in title
    assert "96%" in body


def test_a_warning_is_labelled_high_not_critical(service):
    svc, tray = service()
    svc.check_thresholds((state("work", 85),))
    assert "high" in tray.messages[0][0]


def test_no_tray_means_no_crash(service):
    svc, tray = service()
    tray.available = False
    svc.check_thresholds((state("personal", 99),))
    assert tray.messages == []


def test_repeated_polls_at_the_same_level_stay_quiet(service):
    """The poll loop calls this every interval; it must not alert every time."""
    svc, tray = service()
    for _ in range(20):
        svc.check_thresholds((state("personal", 96, seven=50),))
    assert len(tray.messages) == 1
